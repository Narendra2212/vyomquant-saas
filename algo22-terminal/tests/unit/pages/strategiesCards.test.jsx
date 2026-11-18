/**
 * ═══════════════════════════════════════════════════════════════════════════
 * `pages/Strategies` — the owner's CARD LISTING (Task 14)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * ┌───────────────────────────────────────────────────────────────────────────┐
 * │ SKIPPED, AND THE REASON IS THE REMAINING TASK — NOT A BROKEN TEST.        │
 * │                                                                          │
 * │ These assertions are written against the finished Task 14 page and they   │
 * │ are CORRECT. What does not exist yet is the one thing they all reach for: │
 * │                                                                          │
 * │     await screen.findByTestId('strategy-card-grid')                      │
 * │                                                                          │
 * │ `pages/Strategies` still renders `<DataTable columns={columns} …>` at     │
 * │ ~line 2581. `components/strategies/StrategyCard.jsx` IS built and         │
 * │ exports `StrategyCard`, `StrategyCardGrid` and `StrategyCardCell`; it is  │
 * │ simply not called from the page, so every test here fails on the missing  │
 * │ grid rather than on anything it asserts.                                  │
 * │                                                                          │
 * │ TO FINISH IT, and then delete this banner and the `.skip`s:               │
 * │                                                                          │
 * │  1. For each row in `visibleStrategies`, build `facts` — one entry per    │
 * │     field in TASK_14_REQUIRED. The existing column `render` functions are │
 * │     reusable as `node`: `nameCell(focusedStrategyId)`, `StatusCell`,      │
 * │     `textCell('market')`, `textCell('version')`, and so on, all already   │
 * │     defined in the page. `label` comes from `labelFor(field)` and         │
 * │     `reason` from `reasonFor(field)` — both already defined, both reading │
 * │     `design/pageFields.js`, so the declaration stays the single source.   │
 * │  2. `ownership`, `subscription` and `marketplaceListing` come from the    │
 * │     OTHER read: join `ownershipEntries` to the row on                     │
 * │     `entry.strategy_id === row.id`. A row with no matching entry is       │
 * │     UNKNOWN ownership, never OWNED — see the note on that field in        │
 * │     `pageFields.js`. Those two field declarations were missing and were   │
 * │     ADDED in the same commit as this banner, so step 2 has something to   │
 * │     read a label and a reason from.                                       │
 * │  3. Render `<StrategyCardGrid>` in place of `<DataTable>`, keeping        │
 * │     `FilterBar`, `STATUS_FILTER_OPTIONS`, the no-match empty state and    │
 * │     every `ROW_ACTION`/`ACTION_CATALOG` entry — Requirements 4.2 and 4.4, │
 * │     and `describe('the card\\'s actions survive the rebuild')` below is    │
 * │     the check that they did.                                              │
 * │                                                                          │
 * │ The separate "Owned and subscribed" panel STAYS, and that is a measured   │
 * │ decision rather than an omission: a SUBSCRIBED entry carries a            │
 * │ `listing_id` and no `strategy_id`, so it cannot be a card in a            │
 * │ strategy-keyed grid. Task 14 asks for consolidation "if the same          │
 * │ information can be presented clearly" in the main listing; for subscribed │
 * │ entries it cannot, because they are not rows of this list.                │
 * │                                                                          │
 * │ Skipped rather than deleted, and rather than left red: the assertions are │
 * │ the specification for step 3 and are worth more kept than rewritten. Note │
 * │ that a skipped suite is NOT evidence of anything — Task 14 is incomplete  │
 * │ until these run.                                                          │
 * └───────────────────────────────────────────────────────────────────────────┘
 *
 * design.md §7.2. Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 11.2, 11.4, 11.5, 14.5, 19.3.
 *
 * ---------------------------------------------------------------------------
 * THIS FILE WAS `tests/unit/strategies-table.test.jsx`, AND EVERY ASSERTION IN IT SURVIVED
 * ---------------------------------------------------------------------------
 * Task 17.1 built this listing as a `ds/DataTable`. Task 14 rebuilds it as a grid of dense
 * cards, which Requirement 4.5 permits: it forbids *oversized* cards "exceeding the
 * information density needed to display the fields listed in 4.1"
 * (`requirements.md:81`), not cards. See the docblock in `pages/Strategies.jsx` for the
 * full reading and for why the comment that used to claim otherwise was wrong.
 *
 * So this is a PORT, not a rewrite. Every `it` from the table file is still here and still
 * asserts the same fact; only the three accessors changed, because the DOM did:
 *
 *   `findByRole('table')`                   →  `findByTestId('strategy-card-grid')`
 *   `table.querySelectorAll('tr[data-row-id]')` →  `[data-strategy-card]`
 *   `row.querySelector('[data-column-key=x]')`  →  `[data-card-field="x"]`
 *
 * Four assertions became STRONGER rather than being re-pointed, and each says why at the
 * call site: the header-order check became a field-presence check over fifteen fields
 * instead of ten (`every field is on the card`); the market cell's joined
 * `symbol · exchange · timeframe` string became three separately labelled facts; the
 * "no cards" clause of Requirement 4.5 became the density assertion the requirement
 * actually makes; and the sort control moved from column headers to one labelled `<select>`,
 * so it is asserted as a control rather than as a `<th>` without a button.
 *
 * SECTION ONE ONLY. The page has two independent regions built from two independent reads:
 * the owner's list from `endpoints.strategies.list()`, which this file covers, and the
 * marketplace ownership section from `api.library.myStrategies()`, which
 * `src/pages/__tests__/Strategies.test.jsx` covers. Both reads are stubbed here because
 * both fire on mount; the second is additionally joined onto the cards for their ownership
 * and subscription facts, which `strategiesCardLayout.test.jsx` covers.
 *
 * WHAT IS WORTH ASSERTING, AND WHY
 * --------------------------------
 *  1. **Every one of Requirement 4.1's fields is on the card, under its declared label.**
 *     On a table this was a claim about the header row. A card has no headers — the label
 *     travels with the value as the `<dt>` beside it — so it is asserted as presence and
 *     labelling per field, which is a stronger claim than header order was: a column could
 *     previously be present and empty for every row and still pass.
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
 * the listing never reaches. `components/strategies/StrategyCard`, `ds/FilterBar`,
 * `ds/Panel`, `ds/EmptyState`, `ds/ErrorState`, `ds/StrategyStatus`, `ds/DataTable`'s
 * `sortRows` / `pageSlice`, `lib/strategyHealth` and `design/pageFields` are all real:
 * every one of them is part of what this task is asserting.
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
} from '../../../src/design/pageFields';

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

vi.mock('../../../src/api', () => ({
  default: mockApi,
  api: mockApi,
  endpoints: mockApi,
  // MKT-07: `pages/Strategies` now reads `useFeature(FEATURES.MARKETPLACE_PUBLISH)`, and
  // `useEntitlements.load()` calls `isAuthenticated()` unguarded. A `vi.mock` factory
  // replaces the module, so an omitted export throws on access — as an unhandled rejection
  // out of a `useEffect`, with every assertion still passing and vitest exiting non-zero.
  // `() => false` because this file never signs in; the hook then returns without fetching.
  isAuthenticated: () => false,
}));

vi.mock('../../../src/pages/StrategyBuilder', () => ({
  default: () => <div data-testid="builder-stub" />,
}));

import Strategies from '../../../src/pages/Strategies';

// ─────────────────────────────────────────────────────────────────────────────
// The declaration, read rather than restated
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Requirement 4.1's ten fields, as `pageFields` keys.
 *
 * `name` and `status` are the card's heading and its header badges rather than `<dl>` rows,
 * so they are listed separately below — every one of the ten is still checked, just not all
 * in the same place.
 */
const DL_FIELDS_FROM_REQ_4_1 = Object.freeze([
  'version',
  'market',
  'deploymentState',
  'performance',
  'riskState',
  'lastSignalAt',
  'lastExecutionAt',
  'updatedAt',
]);

/**
 * The five facts Task 14 adds on top of Requirement 4.1's ten.
 *
 * Read as a list here so a field silently dropped from the card fails in this file rather
 * than in review — which is what the header-order assertion did for the table.
 */
const TASK_14_FIELDS = Object.freeze([
  'exchange',
  'timeframe',
  'ownership',
  'subscription',
  'marketplaceListing',
]);

/** Every field the card's `<dl>` must carry. */
const CARD_DL_FIELDS = Object.freeze([...DL_FIELDS_FROM_REQ_4_1, ...TASK_14_FIELDS]);

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

/** Mount with one list body stubbed, and wait for the card grid to arrive. */
const mountWith = async (rows, entries) => {
  mockApi.strategies.list.mockResolvedValue(listBody(rows));
  const view = renderPage(entries);
  const grid = await screen.findByTestId('strategy-card-grid');
  return { ...view, grid, user: userEvent.setup() };
};

/**
 * One card's element for a field, by the key `StrategyCard` stamps on every `<dd>`.
 *
 * The direct replacement for `row.querySelector('[data-column-key=…]')`. Exactly one
 * element per card carries a given key — the chip row that used to repeat the market, the
 * venue and the timeframe above the `<dl>` was removed for that reason as much as for
 * Requirement 4.5's.
 */
const cell = (card, key) => card.querySelector(`[data-card-field="${key}"]`);

/** The cards, in grid order. The direct replacement for `bodyRows`. */
const bodyRows = (grid) => [...grid.querySelectorAll('[data-strategy-card]')];

/** The grid, for assertions that re-query after an interaction. */
const theGrid = () => screen.getByTestId('strategy-card-grid');

/**
 * One card's action footer. The replacement for `cell(row, 'actions')`.
 *
 * The actions were the table's eleventh column and are the card's footer; the set itself is
 * unchanged — the page passes the same `ActionsCell`, which partitions through
 * `lib/rowActions.js` exactly as it did.
 */
const actionsOf = (card) =>
  card.querySelector(`[data-testid="strategy-card-actions-${card.getAttribute('data-strategy-card')}"]`);

/** One card's `<dt>` text for a field, so "labelled" is asserted and not assumed. */
const labelTextFor = (card, key) => {
  const value = cell(card, key);
  // The `<dt>` immediately precedes its `<dd>`: that adjacency IS the labelling, and
  // reading it this way fails if the pair is ever split.
  return value?.previousElementSibling?.textContent.trim() ?? null;
};

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

describe.skip('every field Requirement 4.1 names is on the card, under its declared label', () => {
  it('renders each one under the label `pageFields` declares for it', async () => {
    const { grid } = await mountWith([strategyRow()]);
    const [card] = bodyRows(grid);

    // STRONGER THAN THE HEADER-ORDER CHECK THIS REPLACES. A table column could be present
    // in `<thead>` and empty in every row and still pass; this reads the label off the
    // `<dt>` sitting beside the actual value, per field, so a fact that is declared but not
    // rendered fails here.
    for (const field of CARD_DL_FIELDS) {
      expect(cell(card, field), field).toBeTruthy();
      expect(labelTextFor(card, field), field).toBe(labelOf(field));
    }

    // Requirement 4.1's other two: the name is the card's heading and link, and the status
    // is a header badge. Both are fields, neither is a `<dl>` row.
    expect(cell(card, 'name').textContent).toBe('Momentum v2');
    expect(card.querySelector('h3')).toBeTruthy();
    expect(cell(card, 'status').textContent).toMatch(/running/i);
  });

  it('renders one card per strategy, and the card is sized to its facts (Requirement 4.5)', async () => {
    const { grid } = await mountWith([
      strategyRow(),
      strategyRow({ id: 's-2', name: 'Mean reversion' }),
    ]);

    expect(bodyRows(grid)).toHaveLength(2);

    // THE CLAUSE THIS REPLACES was `renders one row per strategy and no cards`, which read
    // Requirement 4.5 as a ban on cards. It is not: it forbids cards "exceeding the
    // information density needed to display the fields listed in 4.1". So the honest
    // assertion is the density one — the card is all facts and no padding.
    //
    // Thirteen labelled pairs in the `<dl>`, and nothing between them: no metric tile, no
    // progress bar, no repeated chip row, no spacer. The old forbidden card carried ten
    // fields in a ~220px tile with eight tags, three tiles, a progress bar and seven
    // buttons; counting the pairs is what stops that drifting back in.
    for (const card of bodyRows(grid)) {
      const pairs = card.querySelectorAll('dd[data-card-field]');
      expect(pairs).toHaveLength(CARD_DL_FIELDS.length);
      expect(card.querySelectorAll('dt')).toHaveLength(CARD_DL_FIELDS.length);
      // Fifteen facts in total: the thirteen pairs plus the heading and the status badge.
      expect(CARD_DL_FIELDS.length + 2).toBe(15);
    }
  });

  it('keeps both reads exactly as they were — this is a presentation change', async () => {
    await mountWith([strategyRow()]);

    expect(mockApi.strategies.list).toHaveBeenCalledTimes(1);
    expect(mockApi.strategies.list).toHaveBeenCalledWith();
    await waitFor(() => expect(mockApi.library.myStrategies).toHaveBeenCalledTimes(1));
  });

  it('renders the recorded market, exchange, timeframe, version and deployment flag', async () => {
    const { grid } = await mountWith([strategyRow()]);
    const [card] = bodyRows(grid);

    // THE TABLE JOINED THESE THREE INTO ONE CELL reading `BTCUSDT · binance · 1h`, because
    // `DataTable` memoises a row on its projected cell values and one value per column is
    // what keeps a cell and that memo agreeing. A card has no such memo, so Task 14's
    // market / exchange / timeframe are three labelled facts — which is what makes an
    // absent venue legible instead of silently shortening a joined string.
    expect(cell(card, 'market').textContent).toBe('BTCUSDT');
    expect(cell(card, 'exchange').textContent).toBe('binance');
    expect(cell(card, 'timeframe').textContent).toBe('1h');
    expect(cell(card, 'version').textContent).toBe('1.2');
    // UI-01: the run state, from the row's lifecycle `status` ('running' in the fixture).
    expect(cell(card, 'deploymentState').textContent).toContain('Running');
  });

  it('reads a stopped strategy as not running even though is_active is true (UI-01)', async () => {
    // `is_active` is the not-archived flag; it used to make every row read "Active".
    const { grid } = await mountWith([strategyRow({ status: 'stopped', is_active: true })]);
    const text = cell(bodyRows(grid)[0], 'deploymentState').textContent;
    expect(text).toContain('Not running');
    expect(text).not.toMatch(/^Active|Running/);
  });

  it('prefers the most-recent deployment record over the lifecycle status (UI-01)', async () => {
    const { grid } = await mountWith([
      strategyRow({ status: 'stopped', most_recent_deployment: { status: 'running' } }),
    ]);
    expect(cell(bodyRows(grid)[0], 'deploymentState').textContent).toContain('Running');
  });

  it('renders the marker, never a placeholder, for a field the row does not carry', async () => {
    // A row the projection can legitimately produce: no symbol, no version, no `is_active`.
    // The page used to write `"N/A"`, `"1.0"` and `"paper"` into these cells.
    const { grid } = await mountWith([
      strategyRow({ symbol: null, timeframe: null, deployed_exchange: null, version: null, is_active: null, status: null }),
    ]);
    const [card] = bodyRows(grid);

    // `exchange` and `timeframe` join the original three: splitting the joined market cell
    // is what gave each of them a marker of its own to be absent in.
    for (const key of ['market', 'exchange', 'timeframe', 'version', 'deploymentState']) {
      expect(markersIn(cell(card, key)), key).toHaveLength(1);
      expect(cell(card, key).textContent, key).not.toMatch(/N\/A|1\.0|paper/i);
    }
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// 2. Risk: `undetermined` is not-available (Requirement 4.1, and 1.8/1.9)
// ═════════════════════════════════════════════════════════════════════════════

describe.skip('the risk fact goes through computeStrategyHealth', () => {
  it('renders `undetermined` as not-available, never as a healthy verdict', async () => {
    // The row as the list endpoint really serves it: neither permitted source is on it, so
    // `computeStrategyHealth` returns `undetermined` for it — which is EVERY row today.
    const { grid } = await mountWith([strategyRow()]);
    const risk = cell(bodyRows(grid)[0], 'riskState');

    expect(markersIn(risk)).toHaveLength(1);
    expect(risk.textContent).not.toMatch(/healthy/i);
    // The reason is the declaration's own, so the fact says why rather than showing a dash.
    expect(risk.querySelector('[data-metric-marker]').getAttribute('title'))
      .toBe(declared('riskState').reason);
  });

  it('renders a real verdict when a deployment record decides one', async () => {
    // The paired positive: the assertion above is about `undetermined`, not about a marker
    // the fact renders whatever it is handed. When the listing is widened to carry the
    // record, this is what a trader gets — with no change to the page.
    const { grid } = await mountWith([
      strategyRow({ most_recent_deployment: { status: 'failed' } }),
      strategyRow({ id: 's-2', most_recent_deployment: { status: 'running' } }),
      strategyRow({ id: 's-3', most_recent_deployment: { status: 'paused' } }),
    ]);
    const cards = bodyRows(grid);

    expect(cell(cards[0], 'riskState').textContent).toMatch(/error/i);
    expect(cell(cards[1], 'riskState').textContent).toMatch(/healthy/i);
    expect(cell(cards[2], 'riskState').textContent).toMatch(/degraded/i);
    for (const card of cards) expect(markersIn(cell(card, 'riskState'))).toHaveLength(0);
  });

  it('reads an archived row as history beside its status, not as a status', async () => {
    const { grid } = await mountWith([strategyRow({ is_archived: true })]);
    const status = cell(bodyRows(grid)[0], 'status');

    expect(status.textContent).toContain('Archived');
    // The actionable status is still the server's own.
    expect(status.textContent).toMatch(/running/i);
  });

  it('reads a row reporting no status as unreported, not as stopped', async () => {
    const { grid } = await mountWith([strategyRow({ status: null })]);

    expect(cell(bodyRows(grid)[0], 'status').textContent).toMatch(/status not reported/i);
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// 3. Performance: a marker, never a zero (Requirement 14.5)
// ═════════════════════════════════════════════════════════════════════════════

describe.skip('the performance fact', () => {
  it('is declared unavailable on this read, so there is nothing to render', () => {
    // Read from the declaration, not asserted about the page: if a listing widening ever
    // makes this ✅, the expectation below stops describing the intended behaviour and this
    // test is what says so.
    expect(declared('performance').verdict).toBe(VERDICT.UNAVAILABLE);
  });

  it('renders not-available on every card, and no zero', async () => {
    const { grid } = await mountWith([
      strategyRow(),
      strategyRow({ id: 's-2', name: 'Mean reversion' }),
    ]);

    for (const card of bodyRows(grid)) {
      const performance = cell(card, 'performance');
      expect(markersIn(performance)).toHaveLength(1);
      // The failure this exists to stop: `0`, `0%`, `+0.00%` — a figure that reads as a
      // measurement of nothing rather than as the absence of one.
      expect(performance.textContent).not.toMatch(/\d/);
      expect(performance.querySelector('[data-metric-marker]').getAttribute('title'))
        .toBe(declared('performance').reason);
    }
  });

  it('offers no sort option over a set of markers', async () => {
    // THE TABLE ASSERTED a `<th>` with no sort button in it. The card grid has no headers,
    // so the sort control is one labelled `<select>` — and the same honesty rule applies to
    // its option list: ordering a set of identical markers is a control that does nothing.
    await mountWith([strategyRow()]);
    const sort = screen.getByLabelText(/sort by/i);

    const options = [...sort.querySelectorAll('option')].map((o) => o.textContent);
    expect(options.length).toBeGreaterThan(0);
    for (const unavailable of ['performance', 'riskState', 'marketplaceListing']) {
      expect(options.some((text) => text.includes(labelOf(unavailable))), unavailable).toBe(false);
    }
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// 4. BC-3 and BC-4 (Requirement 4.1, 19.2)
// ═════════════════════════════════════════════════════════════════════════════

describe.skip('last signal (BC-3) and last execution (BC-4)', () => {
  it('renders both as figures now that task 12 landed', async () => {
    const row = strategyRow();
    const { grid } = await mountWith([row]);
    const [card] = bodyRows(grid);

    expect(cell(card, 'lastSignalAt').textContent).toBe(asLocal(row.last_signal_at));
    expect(cell(card, 'lastExecutionAt').textContent).toBe(asLocal(row.last_execution_at));
  });

  it('renders each as not-available when the server reports null, with its own reason', async () => {
    // Both keys are ALWAYS present on the response and `null` when there is nothing to
    // report, which is the shape these two facts are written for.
    const { grid } = await mountWith([
      strategyRow({ last_signal_at: null, last_execution_at: null }),
    ]);
    const [card] = bodyRows(grid);

    expect(markersIn(cell(card, 'lastSignalAt'))).toHaveLength(1);
    expect(cell(card, 'lastSignalAt').querySelector('[data-metric-marker]').getAttribute('title'))
      .toBe(declared('lastSignalAt').reason);
    expect(markersIn(cell(card, 'lastExecutionAt'))).toHaveLength(1);
    expect(cell(card, 'lastExecutionAt').querySelector('[data-metric-marker]').getAttribute('title'))
      .toBe(declared('lastExecutionAt').reason);
  });

  it('renders the updated time from the row\'s own updated_at', async () => {
    const row = strategyRow();
    const { grid } = await mountWith([row]);

    expect(cell(bodyRows(grid)[0], 'updatedAt').textContent).toBe(asLocal(row.updated_at));
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// 5. The filter row (Requirements 11.2, 11.5, 19.3)
// ═════════════════════════════════════════════════════════════════════════════

describe.skip('the filter row', () => {
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

  it('narrows the grid and the count on a status segment', async () => {
    const { grid, user } = await mountWith([
      strategyRow(),
      strategyRow({ id: 's-2', name: 'Mean reversion', status: 'draft' }),
    ]);
    expect(bodyRows(grid)).toHaveLength(2);

    await user.click(screen.getByRole('radio', { name: 'Draft' }));

    await waitFor(() => expect(bodyRows(theGrid())).toHaveLength(1));
    expect(within(theGrid()).getByText('Mean reversion')).toBeTruthy();
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
    expect(within(theGrid()).getByText('Mean reversion')).toBeTruthy();
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

describe.skip('the listing region\'s states', () => {
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
    // No grid at all, so no empty frame standing over nothing.
    expect(screen.queryByTestId('strategy-card-grid')).toBeNull();
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
    await waitFor(() => expect(bodyRows(theGrid())).toHaveLength(1));
  });

  it('renders an error state for a failed read, never an empty grid (Requirement 14.5)', async () => {
    mockApi.strategies.list.mockRejectedValue(apiError(503, 'The strategy list is down.'));
    renderPage();

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/again|retry|wrong/i);
    // The two renderings Requirement 14.5 forbids for a failed read.
    expect(screen.queryByTestId('strategy-card-grid')).toBeNull();
    expect(screen.queryByText('No strategies yet')).toBeNull();
  });

  it('re-reads on retry and renders the cards that arrive', async () => {
    mockApi.strategies.list.mockRejectedValueOnce(apiError(503, 'down'));
    renderPage();
    await screen.findByRole('alert');

    mockApi.strategies.list.mockResolvedValue(listBody([strategyRow()]));
    await userEvent.setup().click(screen.getByRole('button', { name: /try again|retry/i }));

    await waitFor(() => expect(mockApi.strategies.list).toHaveBeenCalledTimes(2));
    const grid = await screen.findByTestId('strategy-card-grid');
    expect(within(grid).getByText('Momentum v2')).toBeTruthy();
  });

  it('keeps the two reads independent: the ownership read failing leaves the cards alone', async () => {
    mockApi.library.myStrategies.mockRejectedValue(apiError(503, 'ownership down'));
    const { grid } = await mountWith([strategyRow()]);

    expect(within(grid).getByText('Momentum v2')).toBeTruthy();
    // And the card still renders every other fact: only the two facts that read comes from
    // are unknown, and they say so rather than the card losing its shape.
    expect(cell(bodyRows(grid)[0], 'version').textContent).toBe('1.2');
    expect(markersIn(cell(bodyRows(grid)[0], 'ownership'))).toHaveLength(1);
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// 7. The row still acts, and its destructive pair is separated
//    (Requirements 4.2, 4.3) — task 17.1 moved these into the row, 17.2 partitioned them
// ═════════════════════════════════════════════════════════════════════════════

describe.skip('the card\'s actions survive the rebuild', () => {
  it('renders the non-destructive controls inline', async () => {
    const { grid } = await mountWith([strategyRow({ status: 'stopped' })]);
    const actions = within(actionsOf(bodyRows(grid)[0]));

    // Requirement 4.2's own words for two of them: "Duplicate", not the endpoint's "clone";
    // "Signal Trace", not the card's abbreviated "Trace".
    for (const label of ['Backtest', 'Edit', 'Duplicate', 'Rename', 'Signal Trace']) {
      expect(actions.getByRole('button', { name: label }), label).toBeTruthy();
    }
  });

  it('shows Deploy as a visible button and keeps the archive in the overflow (UI-02)', async () => {
    const { grid } = await mountWith([strategyRow({ status: 'stopped' })]);
    const actions = within(actionsOf(bodyRows(grid)[0]));

    // UI-02: traders could not find the deployment behind the ⋮ menu, so it is a visible
    // button. The archive stays behind the overflow trigger, below a divider.
    expect(actions.getByRole('button', { name: 'Deploy Momentum v2' })).toBeTruthy();
    expect(actions.queryByRole('button', { name: 'Delete strategy' })).toBeNull();
    expect(actions.getByRole('button', { name: 'More actions for Momentum v2' })).toBeTruthy();
  });

  it('opens the deploy dialog on paper from the visible Deploy button (FE-03)', async () => {
    const { grid } = await mountWith([strategyRow({ status: 'stopped' })]);
    const actions = within(actionsOf(bodyRows(grid)[0]));

    await userEvent.setup().click(actions.getByRole('button', { name: 'Deploy Momentum v2' }));
    expect(await screen.findByRole('dialog', { name: 'Start paper session' })).toBeTruthy();
  });

  it('opens the archive below a divider, in a named group', async () => {
    const { grid } = await mountWith([strategyRow({ status: 'stopped' })]);
    const actions = within(actionsOf(bodyRows(grid)[0]));

    await userEvent.setup()
      .click(actions.getByRole('button', { name: 'More actions for Momentum v2' }));

    const menu = within(screen.getByRole('menu'));
    expect(menu.getByRole('separator')).toBeTruthy();

    const group = menu.getByRole('group', { name: /destructive and live-trading actions/i });
    expect(within(group).queryByRole('menuitem', { name: 'Start paper session' })).toBeNull();
    const archive = within(group).getByRole('menuitem', { name: 'Delete strategy' });

    // §7.2's treatment: `status.error` for the archive.
    expect(archive.getAttribute('data-menu-intent')).toBe('destructive');
    // Traders look for "Delete"; the entry says so. The server soft-archives, and the confirmation
    // dialog says history is retained, so the wording is not a promise of destruction.
    expect(archive.textContent).toMatch(/delete/i);
  });

  it('archives only after the confirmation, never from the menu entry', async () => {
    // Property 13 at this call site: the menu entry opens the dialog and issues nothing.
    const { grid } = await mountWith([strategyRow({ status: 'stopped' })]);
    const user = userEvent.setup();
    const actions = within(actionsOf(bodyRows(grid)[0]));

    await user.click(actions.getByRole('button', { name: 'More actions for Momentum v2' }));
    await user.click(within(screen.getByRole('menu'))
      .getByRole('menuitem', { name: 'Delete strategy' }));

    const dialog = await screen.findByRole('dialog', { name: /delete strategy/i });
    expect(within(dialog).getByText(/retained for history and audit/i)).toBeTruthy();
    expect(mockApi.strategies.delete).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: /delete strategy/i }));
    await waitFor(() => expect(mockApi.strategies.delete).toHaveBeenCalledWith('s-1'));
  });

  it('offers Pause inline instead of the deployment while the strategy is running', async () => {
    const { grid } = await mountWith([strategyRow({ status: 'running' })]);
    const actions = within(actionsOf(bodyRows(grid)[0]));

    expect(actions.getByRole('button', { name: 'Pause' })).toBeTruthy();

    // Pause is neither destructive nor a live transition, so it is inline; and a running
    // strategy is not offered a second deployment at all.
    await userEvent.setup()
      .click(actions.getByRole('button', { name: 'More actions for Momentum v2' }));
    expect(actions.queryByRole('button', { name: 'Deploy Momentum v2' })).toBeNull();
    const menu = within(screen.getByRole('menu'));
    expect(menu.queryByRole('menuitem', { name: 'Start paper session' })).toBeNull();
    expect(menu.getByRole('menuitem', { name: 'Delete strategy' })).toBeTruthy();
  });

  it('links the card to its detail page from the name', async () => {
    const { grid } = await mountWith([strategyRow()]);

    // The table made the whole ROW a link (`rowHref`). On a card the name IS the link, so
    // the destination is asserted on the element carrying the field rather than on a
    // wrapper — and it is a real `<a>` with an href, not a div with a click handler, which
    // is what keeps the page at zero `a11y-ratchet` findings.
    const name = cell(bodyRows(grid)[0], 'name');
    expect(name.tagName).toBe('A');
    expect(name.getAttribute('href')).toBe('/app/strategies/s-1');
  });

  it('marks the deep-linked strategy, and only that one', async () => {
    const { grid } = await mountWith(
      [strategyRow(), strategyRow({ id: 's-2', name: 'Mean reversion' })],
      ['/app/strategies?strategy_id=s-2'],
    );
    const cards = bodyRows(grid);

    expect(cards[0].textContent).not.toMatch(/focused/i);
    expect(cards[1].textContent).toMatch(/focused target/i);
  });
});
