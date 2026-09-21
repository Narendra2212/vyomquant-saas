/**
 * `pages/tradeHistoryFilters` — vyomquant-ui-redesign task 15.2. design.md §7.7.
 * Requirements 11.2, 11.5.
 *
 * The property test over generated row sets and filter combinations is task 15.3's (Property 18).
 * These are the example cases: the empty query, a query matching nothing, each filter alone, the
 * filters combined, a row missing the field a filter asks about, and Requirement 11.5's two empty
 * states — which are the same empty table and are told apart only by `totalCount`.
 *
 * The rows below are the shapes the two real reads produce, not invented ones:
 *
 * * `LIVE_ROWS` are `executions` telemetry rows — the seven columns `log_execution` writes,
 *   `symbol` carrying the `_` separator that path stores, and no `pnl`, `fee`, `slippage` or
 *   `strategy_id` anywhere.
 * * `PAPER_ROWS` are `PaperTradingService._trade_body`'s keys, `realized_pnl` as the exact decimal
 *   string it serialises and `strategy_id: null` exactly as that function writes it.
 */

import { describe, expect, it } from 'vitest';

import { emptyVariantFor } from '../../../src/components/ds/FilterBar';
import {
  ALL_VALUE,
  ANY_VALUE,
  COUNT_NOUN,
  DEFAULT_FILTERS,
  DEFAULT_SEARCH,
  EMPTY_VARIANT,
  ENVIRONMENTS,
  FILTER_IDS,
  OUTCOME_FILTER_VALUES,
  PAPER_REPORTED_FIELDS,
  SIDE_FILTER_VALUES,
  UNREPORTED_ON_LIVE,
  WITHHELD_CAUSES,
  activeFilterCount,
  buildPredicate,
  emptyVariant,
  fieldAbsenceReason,
  filterControls,
  filterTrades,
  isFieldReported,
  marketOptions,
  matchesOutcome,
  matchesSearch,
  matchesSide,
  normaliseEnvironment,
  normaliseFilters,
  offersClearFilters,
  pnlSign,
  searchControl,
  strategyOptions,
  tradeRowId,
} from '../../../src/pages/tradeHistoryFilters';

/** `executions` telemetry rows: seven columns, `_` separator, no P&L and no strategy. */
const LIVE_ROWS = [
  { timestamp: '2024-05-01T10:00:00Z', user_id: 'u1', symbol: 'BTC_USDT', side: 'buy', status: 'filled', amount: '0.5', price: '64000' },
  { timestamp: '2024-05-01T11:00:00Z', user_id: 'u1', symbol: 'ETH_USDT', side: 'sell', status: 'filled', amount: '3', price: '3100' },
  { timestamp: '2024-05-01T12:00:00Z', user_id: 'u1', symbol: 'BTC_USDT', side: 'sell', status: 'rejected', amount: '0.2', price: '63800' },
];

/** `_trade_body`'s keys. `realized_pnl` is exact decimal text; `strategy_id` is always null. */
const PAPER_ROWS = [
  {
    execution_id: 'fill-1', order_id: 'order-1', user_id: 'u1', strategy_id: null, deployment_id: null,
    symbol: 'BTC/USDT', side: 'buy', quantity: '0.5', price: '64000.0000000000', fee: '3.20',
    realized_pnl: '128.4500000000', status: 'FILLED', executed_at: '2024-05-01T10:00:00Z',
    execution_environment: 'PAPER', is_simulated: true,
  },
  {
    execution_id: 'fill-2', order_id: 'order-2', user_id: 'u1', strategy_id: null, deployment_id: null,
    symbol: 'ETH/USDT', side: 'sell', quantity: '3', price: '3100.0000000000', fee: '1.10',
    realized_pnl: '-42.0000000000', status: 'FILLED', executed_at: '2024-05-01T11:00:00Z',
    execution_environment: 'PAPER', is_simulated: true,
  },
  {
    execution_id: 'fill-3', order_id: 'order-2', user_id: 'u1', strategy_id: null, deployment_id: null,
    symbol: 'ETH/USDT', side: 'sell', quantity: '1', price: '3105.0000000000', fee: '0.40',
    realized_pnl: '0.0000000000', status: 'FILLED', executed_at: '2024-05-01T11:30:00Z',
    execution_environment: 'PAPER', is_simulated: true,
  },
];

/** A paper ledger that does carry strategy labels, for the day `strategy_id` is written. */
const LABELLED_ROWS = [
  { ...PAPER_ROWS[0], strategy_id: 'Momentum v2' },
  { ...PAPER_ROWS[1], strategy_id: 'mean reversion' },
  { ...PAPER_ROWS[2], strategy_id: 'Momentum v2' },
];

const idsOf = (rows) => rows.map((row, index) => tradeRowId(row, index));

const view = (rows, options) => filterTrades(rows, options);

describe('the declaration this module reads from pageFields', () => {
  it('takes the four unavailable live fields from pageFields rather than restating them', () => {
    expect([...UNREPORTED_ON_LIVE].sort()).toEqual(['fees', 'pnl', 'slippage', 'strategy']);
  });

  it('reports market and side on both reads, and P&L on paper only', () => {
    expect(isFieldReported('market', ENVIRONMENTS.LIVE)).toBe(true);
    expect(isFieldReported('side', ENVIRONMENTS.LIVE)).toBe(true);
    expect(isFieldReported('pnl', ENVIRONMENTS.LIVE)).toBe(false);
    expect(isFieldReported('pnl', ENVIRONMENTS.PAPER)).toBe(true);
    expect(isFieldReported('strategy', ENVIRONMENTS.LIVE)).toBe(false);
    // Slippage is unreported on BOTH reads, which is why it is not in the paper set.
    expect(PAPER_REPORTED_FIELDS).not.toContain('slippage');
    expect(isFieldReported('slippage', ENVIRONMENTS.PAPER)).toBe(false);
  });

  it('carries a reason for every field it will not offer a control for', () => {
    for (const field of UNREPORTED_ON_LIVE) {
      expect(fieldAbsenceReason(field)).toMatch(/\S/);
    }
  });

  it('treats an unknown environment as live, the read that reports less', () => {
    expect(normaliseEnvironment(undefined)).toBe(ENVIRONMENTS.LIVE);
    expect(normaliseEnvironment('paper')).toBe(ENVIRONMENTS.PAPER);
    expect(normaliseEnvironment('PAPER')).toBe(ENVIRONMENTS.PAPER);
    expect(normaliseEnvironment('nonsense')).toBe(ENVIRONMENTS.LIVE);
  });
});

describe('the empty query matches everything', () => {
  it('keeps every live row under the default filters', () => {
    const result = view(LIVE_ROWS, { filters: DEFAULT_FILTERS, search: DEFAULT_SEARCH, environment: 'live' });
    expect(idsOf(result.rows)).toEqual(idsOf(LIVE_ROWS));
    expect(result.totalCount).toBe(3);
    expect(result.resultCount).toBe(3);
    expect(result.emptyVariant).toBeNull();
    expect(result.hasActiveFilters).toBe(false);
    expect(result.activeFilterCount).toBe(0);
  });

  it('keeps every paper row, and a whitespace-only query is still the empty query', () => {
    const result = view(PAPER_ROWS, { search: '   ', environment: 'paper' });
    expect(result.resultCount).toBe(PAPER_ROWS.length);
    expect(result.activeFilterCount).toBe(0);
    expect(matchesSearch(PAPER_ROWS[0], '')).toBe(true);
    expect(matchesSearch(PAPER_ROWS[0], '   ')).toBe(true);
  });

  it('is what an absent options argument means too', () => {
    expect(view(PAPER_ROWS).resultCount).toBe(PAPER_ROWS.length);
  });
});

describe('a query matching nothing', () => {
  it('empties the result while leaving the total intact', () => {
    const result = view(LIVE_ROWS, { search: 'SOL/USDT', environment: 'live' });
    expect(result.rows).toEqual([]);
    expect(result.resultCount).toBe(0);
    expect(result.totalCount).toBe(3);
    expect(result.emptyVariant).toBe(EMPTY_VARIANT.NO_MATCH);
    expect(result.offersClearFilters).toBe(true);
    expect(result.activeFilterCount).toBe(1);
  });
});

describe('the search', () => {
  it('finds a market across both spellings of the separator', () => {
    // The telemetry path stores `BTC_USDT`; the paper read and ccxt use `BTC/USDT`.
    expect(view(LIVE_ROWS, { search: 'btc/usdt', environment: 'live' }).resultCount).toBe(2);
    expect(view(PAPER_ROWS, { search: 'btc_usdt', environment: 'paper' }).resultCount).toBe(1);
    expect(view(LIVE_ROWS, { search: 'btcusdt', environment: 'live' }).resultCount).toBe(2);
  });

  it('is case-insensitive and matches on a substring', () => {
    expect(view(LIVE_ROWS, { search: 'ETH', environment: 'live' }).resultCount).toBe(1);
    expect(view(LIVE_ROWS, { search: 'eth', environment: 'live' }).resultCount).toBe(1);
    expect(view(LIVE_ROWS, { search: 'usdt', environment: 'live' }).resultCount).toBe(3);
  });

  it('searches the strategy label when a row carries one', () => {
    expect(view(LABELLED_ROWS, { search: 'momentum', environment: 'paper' }).resultCount).toBe(2);
    expect(view(LABELLED_ROWS, { search: 'mean', environment: 'paper' }).resultCount).toBe(1);
  });

  it('names only what is searchable: no strategy in the ledger, no strategy in the label', () => {
    expect(searchControl(LIVE_ROWS, 'live')).toMatchObject({ label: 'Search market', fields: ['market'] });
    // Paper reports the field, but `_trade_body` writes `strategy_id: null` on every row.
    expect(searchControl(PAPER_ROWS, 'paper')).toMatchObject({ label: 'Search market', fields: ['market'] });
    expect(searchControl(LABELLED_ROWS, 'paper')).toMatchObject({
      label: 'Search market or strategy',
      fields: ['market', 'strategy'],
    });
  });
});

describe('each filter alone', () => {
  it('filters by side, on both reads', () => {
    const buys = view(LIVE_ROWS, { filters: { side: 'BUY' }, environment: 'live' });
    expect(buys.resultCount).toBe(1);
    expect(buys.rows[0].symbol).toBe('BTC_USDT');
    expect(view(LIVE_ROWS, { filters: { side: 'SELL' }, environment: 'live' }).resultCount).toBe(2);
    expect(view(PAPER_ROWS, { filters: { side: 'SELL' }, environment: 'paper' }).resultCount).toBe(2);
  });

  it('filters by outcome from the sign of the exact decimal, breakeven in neither', () => {
    expect(pnlSign(PAPER_ROWS[0])).toBe(1);
    expect(pnlSign(PAPER_ROWS[1])).toBe(-1);
    expect(pnlSign(PAPER_ROWS[2])).toBe(0);
    expect(pnlSign({ realized_pnl: '-0.0000000000' })).toBe(0);
    expect(view(PAPER_ROWS, { filters: { outcome: 'PROFIT' }, environment: 'paper' }).resultCount).toBe(1);
    expect(view(PAPER_ROWS, { filters: { outcome: 'LOSS' }, environment: 'paper' }).resultCount).toBe(1);
    // The breakeven row is in neither selection and in `ALL`.
    expect(view(PAPER_ROWS, { filters: { outcome: ALL_VALUE }, environment: 'paper' }).resultCount).toBe(3);
  });

  it('filters by market, one selection covering both spellings', () => {
    expect(view(LIVE_ROWS, { filters: { market: 'BTC/USDT' }, environment: 'live' }).resultCount).toBe(2);
    expect(view(LIVE_ROWS, { filters: { market: 'btc_usdt' }, environment: 'live' }).resultCount).toBe(2);
    expect(view(PAPER_ROWS, { filters: { market: 'ETH/USDT' }, environment: 'paper' }).resultCount).toBe(2);
  });

  it('filters by strategy, case-folded', () => {
    expect(view(LABELLED_ROWS, { filters: { strategy: 'momentum v2' }, environment: 'paper' }).resultCount).toBe(2);
    expect(view(LABELLED_ROWS, { filters: { strategy: 'Mean Reversion' }, environment: 'paper' }).resultCount).toBe(1);
  });

  it('ignores a value outside a filter vocabulary rather than emptying the table', () => {
    expect(view(LIVE_ROWS, { filters: { side: 'LONG' }, environment: 'live' }).resultCount).toBe(3);
    expect(matchesSide(LIVE_ROWS[0], 'nonsense')).toBe(true);
    expect(matchesOutcome(PAPER_ROWS[2], 'nonsense')).toBe(true);
  });
});

describe('the filters combined', () => {
  it('narrows on every dimension at once, which one chip row could not express', () => {
    const rows = [...LABELLED_ROWS, { ...LABELLED_ROWS[0], execution_id: 'fill-4', realized_pnl: '-9.5' }];
    const result = view(rows, {
      filters: { side: 'BUY', outcome: 'LOSS', market: 'BTC/USDT', strategy: 'momentum v2' },
      search: 'btc',
      environment: 'paper',
    });
    expect(idsOf(result.rows)).toEqual(['fill-4']);
    expect(result.activeFilterCount).toBe(5);
    expect(result.totalCount).toBe(4);
  });

  it('agrees with the predicate applied by hand — P18 in one example', () => {
    const options = { filters: { side: 'SELL', market: 'ETH/USDT' }, search: 'eth', environment: 'paper' };
    const predicate = buildPredicate(options);
    expect(idsOf(view(PAPER_ROWS, options).rows)).toEqual(idsOf(PAPER_ROWS.filter(predicate)));
  });

  it('reads every filter as an AND, so an unmatched dimension empties the result', () => {
    const result = view(PAPER_ROWS, {
      filters: { side: 'BUY', outcome: 'LOSS' },
      environment: 'paper',
    });
    expect(result.resultCount).toBe(0);
    expect(result.emptyVariant).toBe(EMPTY_VARIANT.NO_MATCH);
  });
});

describe('a row missing a field a filter asks about', () => {
  const partial = [
    { symbol: 'BTC/USDT' },                                   // no side, no P&L, no strategy
    { side: 'buy' },                                          // no market
    { symbol: 'ETH/USDT', side: 'buy', realized_pnl: 'n/a' }, // an unreadable figure
    { symbol: 'SOL/USDT', side: 'hold', realized_pnl: '5' },  // a side this module does not know
  ];

  it('does not throw on any of them, and includes them all under the default filters', () => {
    const result = view(partial, { environment: 'paper' });
    expect(result.totalCount).toBe(4);
    expect(result.resultCount).toBe(4);
  });

  it('excludes a row with no side from a side selection, and never treats it as a buy', () => {
    expect(view(partial, { filters: { side: 'BUY' }, environment: 'paper' }).resultCount).toBe(2);
    expect(view(partial, { filters: { side: 'SELL' }, environment: 'paper' }).resultCount).toBe(0);
  });

  it('does not count an unreported or unreadable P&L as a loss', () => {
    expect(pnlSign(partial[0])).toBeNull();
    expect(pnlSign(partial[2])).toBeNull();
    expect(view(partial, { filters: { outcome: 'LOSS' }, environment: 'paper' }).resultCount).toBe(0);
    expect(view(partial, { filters: { outcome: 'PROFIT' }, environment: 'paper' }).resultCount).toBe(1);
  });

  it('is total for a non-row, an empty read and a non-array', () => {
    const predicate = buildPredicate({ environment: 'paper' });
    expect(predicate(null)).toBe(false);
    expect(predicate(undefined)).toBe(false);
    expect(predicate('BTC/USDT')).toBe(false);
    expect(predicate([])).toBe(false);
    expect(view([]).totalCount).toBe(0);
    expect(view(null).totalCount).toBe(0);
    expect(view(undefined).resultCount).toBe(0);
    // A non-row is in neither count, so `resultCount <= totalCount` — FilterBar asserts it.
    const mixed = view([null, 'x', LIVE_ROWS[0]], { environment: 'live' });
    expect(mixed.totalCount).toBe(1);
    expect(mixed.resultCount).toBe(1);
  });

  it('identifies rows by what each read actually carries, index last', () => {
    expect(tradeRowId(PAPER_ROWS[0], 7)).toBe('fill-1');
    expect(tradeRowId({ id: 'ccxt-1' }, 7)).toBe('ccxt-1');
    // The seven-column telemetry row has no identifier at all.
    expect(tradeRowId(LIVE_ROWS[0], 2)).toBe(2);
  });
});

describe('a control over a field the read does not report is not offered', () => {
  it('offers side and market on live, and neither outcome nor strategy', () => {
    const ids = filterControls(LIVE_ROWS, 'live').map((control) => control.id);
    expect(ids).toEqual([FILTER_IDS.SIDE, FILTER_IDS.MARKET]);
  });

  it('offers outcome on paper, where the P&L is real', () => {
    const ids = filterControls(PAPER_ROWS, 'paper').map((control) => control.id);
    expect(ids).toEqual([FILTER_IDS.SIDE, FILTER_IDS.OUTCOME, FILTER_IDS.MARKET]);
  });

  it('withholds the strategy select until a row actually carries a label', () => {
    expect(filterControls(PAPER_ROWS, 'paper').map((c) => c.id)).not.toContain(FILTER_IDS.STRATEGY);
    expect(filterControls(LABELLED_ROWS, 'paper').map((c) => c.id)).toContain(FILTER_IDS.STRATEGY);
  });

  it('drops an unanswerable dimension from the applied state instead of emptying the table', () => {
    const stale = { side: ALL_VALUE, outcome: 'PROFIT', market: ANY_VALUE, strategy: 'momentum v2' };
    const applied = normaliseFilters(stale, 'live');
    expect(applied.outcome).toBe(ALL_VALUE);
    expect(applied.strategy).toBe(ANY_VALUE);
    const result = view(LIVE_ROWS, { filters: stale, environment: 'live' });
    expect(result.resultCount).toBe(3);
    expect(result.activeFilterCount).toBe(0);
    // And it is honoured again on the read that reports it.
    expect(normaliseFilters(stale, 'paper').outcome).toBe('PROFIT');
  });

  it('explains every control it withholds, and names which kind of absence it is', () => {
    const live = view(LIVE_ROWS, { environment: 'live' }).withheld;
    expect(live.map((entry) => entry.id)).toEqual([FILTER_IDS.OUTCOME, FILTER_IDS.STRATEGY]);
    // Live: the venue records neither, so the reason is pageFields' own.
    for (const entry of live) {
      expect(entry.cause).toBe(WITHHELD_CAUSES.UNREPORTED);
      expect(entry.reason).toMatch(/\S/);
      expect(entry.reason).toBe(fieldAbsenceReason(entry.field));
    }
    // Paper reports a `strategy_id` key, so the declaration allows it; `_trade_body` writes
    // `null` into it on every row, so there is nothing to choose from.
    const paper = view(PAPER_ROWS, { environment: 'paper' }).withheld;
    expect(paper.map((entry) => entry.id)).toEqual([FILTER_IDS.STRATEGY]);
    expect(paper[0].cause).toBe(WITHHELD_CAUSES.NO_VALUES);
    expect(paper[0].reason).toMatch(/\S/);
  });

  it('offers or explains every dimension, never neither and never both', () => {
    for (const rows of [[], LIVE_ROWS, PAPER_ROWS, LABELLED_ROWS]) {
      for (const environment of ['live', 'paper']) {
        const result = view(rows, { environment });
        const offered = result.controls.map((control) => control.id);
        const withheld = result.withheld.map((entry) => entry.id);
        expect([...offered, ...withheld].sort()).toEqual(Object.values(FILTER_IDS).sort());
        for (const entry of result.withheld) expect(entry.reason).toMatch(/\S/);
      }
    }
  });

  it('builds every control the way FilterBar requires it', () => {
    for (const control of filterControls(LABELLED_ROWS, 'paper')) {
      expect(control.label).toMatch(/\S/);
      expect(['segmented', 'select']).toContain(control.kind);
      expect(control.options.length).toBeGreaterThan(0);
      for (const opt of control.options) expect(opt.label).toMatch(/\S/);
    }
  });

  it('offers no market select when there is nothing to choose between', () => {
    expect(marketOptions([]).map((o) => o.value)).toEqual([ANY_VALUE]);
    expect(filterControls([], 'live').map((c) => c.id)).toEqual([FILTER_IDS.SIDE]);
  });

  it('builds select options from the unfiltered rows, once each, sorted', () => {
    expect(marketOptions(LIVE_ROWS).map((o) => o.value)).toEqual([ANY_VALUE, 'BTC/USDT', 'ETH/USDT']);
    expect(strategyOptions(LABELLED_ROWS).map((o) => o.value))
      .toEqual([ANY_VALUE, 'mean reversion', 'momentum v2']);
    // The label keeps the first spelling seen; the value is the comparison key.
    expect(strategyOptions(LABELLED_ROWS)[2].label).toBe('Momentum v2');
    // The market select still offers every market while one of them is selected.
    const filtered = view(LIVE_ROWS, { filters: { market: 'BTC/USDT' }, environment: 'live' });
    expect(filtered.marketOptions.map((o) => o.value)).toEqual([ANY_VALUE, 'BTC/USDT', 'ETH/USDT']);
  });
});

describe("Requirement 11.5's two empty states", () => {
  it('calls an empty ledger no-data, and offers no clear-filters', () => {
    const result = view([], { environment: 'live' });
    expect(result.totalCount).toBe(0);
    expect(result.resultCount).toBe(0);
    expect(result.emptyVariant).toBe(EMPTY_VARIANT.NO_DATA);
    expect(result.offersClearFilters).toBe(false);
    expect(result.hasActiveFilters).toBe(false);
  });

  it('calls a filtered-out ledger no-match, and offers clear-filters', () => {
    const result = view(LIVE_ROWS, { filters: { market: 'SOL/USDT' }, environment: 'live' });
    expect(result.totalCount).toBe(3);
    expect(result.resultCount).toBe(0);
    expect(result.emptyVariant).toBe(EMPTY_VARIANT.NO_MATCH);
    expect(result.offersClearFilters).toBe(true);
    expect(result.hasActiveFilters).toBe(true);
  });

  it('is no empty state at all while the table has rows', () => {
    expect(emptyVariant(1, 1)).toBeNull();
    expect(emptyVariant(1, 300)).toBeNull();
    expect(offersClearFilters(null)).toBe(false);
    expect(offersClearFilters(EMPTY_VARIANT.NO_DATA)).toBe(false);
    expect(offersClearFilters(EMPTY_VARIANT.NO_MATCH)).toBe(true);
  });

  it('is total for counts that are not counts', () => {
    expect(emptyVariant(undefined, undefined)).toBe(EMPTY_VARIANT.NO_DATA);
    expect(emptyVariant(Number.NaN, 5)).toBe(EMPTY_VARIANT.NO_MATCH);
  });

  it('decides it the same way ds/FilterBar does, so the two cannot drift', () => {
    for (const total of [0, 1, 3, 50]) {
      for (const result of [0, 1, 3, 50]) {
        expect(emptyVariant(result, total)).toBe(emptyVariantFor(result, total));
      }
    }
  });

  it('counts the search and every applied dimension as a reason to offer clear-filters', () => {
    expect(activeFilterCount(DEFAULT_FILTERS, DEFAULT_SEARCH, 'paper')).toBe(0);
    expect(activeFilterCount({ side: 'BUY' }, '', 'paper')).toBe(1);
    expect(activeFilterCount({ side: 'BUY' }, 'btc', 'paper')).toBe(2);
    expect(activeFilterCount({ side: 'BUY', outcome: 'LOSS' }, 'btc', 'paper')).toBe(3);
    // Outcome is not applied on live, so it is not something to clear.
    expect(activeFilterCount({ side: 'BUY', outcome: 'LOSS' }, 'btc', 'live')).toBe(2);
  });
});

describe('the vocabularies are declared data', () => {
  it('freezes them, so a page cannot edit the filter it renders', () => {
    expect(Object.isFrozen(DEFAULT_FILTERS)).toBe(true);
    expect(Object.isFrozen(SIDE_FILTER_VALUES)).toBe(true);
    expect(Object.isFrozen(OUTCOME_FILTER_VALUES)).toBe(true);
    expect(Object.isFrozen(FILTER_IDS)).toBe(true);
    expect(Object.isFrozen(EMPTY_VARIANT)).toBe(true);
    expect(Object.isFrozen(normaliseFilters({}, 'live'))).toBe(true);
  });

  it('states the two axes separately, which is what makes "a buy that lost" expressible', () => {
    expect([...SIDE_FILTER_VALUES]).toEqual([ALL_VALUE, 'BUY', 'SELL']);
    expect([...OUTCOME_FILTER_VALUES]).toEqual([ALL_VALUE, 'PROFIT', 'LOSS']);
    expect(DEFAULT_FILTERS[FILTER_IDS.MARKET]).toBe(ANY_VALUE);
    expect(COUNT_NOUN).toBe('trades');
  });
});
