/**
 * tradeHistoryFilters.js — the pure filter, search and count decisions `TradeHistory.jsx`
 * renders through.
 *
 * vyomquant-ui-redesign task 15.2. design.md §7.7. Requirements 11.2, 11.5. Property P18.
 *
 * Separated from the page for the same reason `paperTradingFormat.js` is: every rule in here is
 * a rule about WHICH ROWS A TRADER IS LOOKING AT, and that has to be assertable on its own.
 * Nothing here touches React, `api`, the DOM or a clock, so `buildPredicate` can be asked about
 * a row without a render, which is the only way Property 18 ("the rendered row id set equals the
 * predicate-satisfying set") can be stated at all — a predicate living inside a `.filter()` call
 * in the middle of a component is not a thing a test can hold.
 *
 * TWO READS, TWO ROW SHAPES, ONE PREDICATE
 * ---------------------------------------
 * The page reads `api.orders.getHistory()` for Live and `api.paper.getTrades(100)` for Paper, and
 * the rows differ:
 *
 * * **Live** — `GET /api/orders/history` is a BARE ARRAY of `executions` telemetry rows, which
 *   `telemetry_engine` creates with exactly seven columns: `(timestamp, user_id, symbol, side,
 *   status, amount, price)`. When that read fails and an `exchange_id` was supplied it falls back
 *   to the **venue's own ccxt trade shape** instead, which is a third-party object shape.
 * * **Paper** — `GET /api/paper/trades` answers `{trades, count, execution_environment,
 *   is_simulated, session_id}`, and `PaperTradingService._trade_body` builds each row key for
 *   key: `execution_id order_id user_id strategy_id deployment_id symbol side quantity price fee
 *   realized_pnl status executed_at`.
 *
 * So every reader below is **total**: a row missing the field a filter asks about yields `null`
 * and is excluded by a specific selection while still being included by "All". It is never
 * defaulted. Today's page does default — `String(row.side ?? "buy")`, `toNumber(row.pnl ?? … ?? 0)`,
 * `row.strat ?? … ?? "Direct"` — which is how an unlabelled fill comes to be a `buy` for `$0`
 * from a strategy called "Direct". A defaulted field is worse than a missing one here, because
 * the filter then matches on a value nothing reported.
 *
 * A CONTROL OVER A FIELD NOTHING REPORTS IS NOT OFFERED (`design/pageFields.js`)
 * -----------------------------------------------------------------------------
 * `pageFields.js` declares four of Trade History's columns UNAVAILABLE on the live read — `pnl`,
 * `fees`, `slippage` and `strategy` — because the seven-column telemetry row carries none of
 * them. Two of those four are filter inputs, and that decides what the controls do:
 *
 * * **The outcome filter** (Profit / Loss) reads P&L. On Live there is no P&L, so the control is
 *   **not rendered** — {@link filterControls} omits it and {@link withheldFilters} reports it with
 *   `pageFields`' own reason, so the page can say why rather than leaving a gap. A Profit/Loss
 *   control over an unreported P&L would either match every row or none, and both are a lie about
 *   the ledger. Requirement 11.2 asks for filters that work, not for filters.
 * * **The strategy select** reads the strategy label. Nothing records one for a live execution,
 *   so it is not offered either. And the Paper read is no better in practice: `_trade_body` sets
 *   `"strategy_id": None` on every row it builds, so even where the key exists the value is
 *   always `null`. That is why the select is gated on **evidence as well as declaration** — it
 *   is offered only when at least one row in hand actually carries a label. A select whose only
 *   option is "Any strategy" is a dead control, and it starts working on its own the day a read
 *   begins reporting the field, with no change here.
 * * **`fees` and `slippage`** are unavailable too, and neither is a filter input, so they change
 *   nothing in this module. They are named here so a reader does not go looking.
 *
 * Every dimension is therefore either a control or a sentence: {@link filterDecisions} produces
 * both lists from one pass, so a control cannot disappear without the page having a reason to
 * show. {@link WITHHELD_CAUSES} tells the two apart — `unreported` is the venue recording
 * nothing, `no-values` is nothing recorded yet.
 *
 * The declaration is READ from `pageFields.js` rather than restated ({@link UNREPORTED_ON_LIVE}),
 * so a verdict that changes there changes the controls here.
 *
 * Availability gates the CONTROLS and the NORMALISATION, never the row set: {@link normaliseFilters}
 * drops a dimension the current environment cannot answer, so a stale `outcome: 'PROFIT'` left
 * over from a Paper session cannot empty a Live table. It is dropped, not applied to a field that
 * does not exist — emptying the table would produce a "no trades match" state for rows that do
 * match everything the trader can actually filter on.
 *
 * SIDE AND OUTCOME ARE TWO AXES, NOT ONE CHIP ROW
 * ----------------------------------------------
 * Today's page has one group — `ALL BUY SELL PROFIT` — in which `PROFIT` and `BUY` are mutually
 * exclusive, so "buy trades that lost" is not expressible. §7.7's sketch draws them as one row of
 * chips; they are declared here as two independent segmented filters, which is what makes the
 * combination possible. Neither vocabulary is invented: `paper_orders.side` is constrained
 * `CHECK (side IN ('buy', 'sell'))` and the ccxt trade shape uses the same two, so `BUY`/`SELL`
 * covers both reads. (`LONG`/`SHORT` is `paper_positions`' vocabulary — a different table, not
 * this page's read.)
 *
 * P&L SIGN IS READ FROM THE DIGITS, NOT FROM A FLOAT
 * -------------------------------------------------
 * `realized_pnl` arrives as an exact decimal string (`NUMERIC(28,10)` rendered by `str(Decimal)`).
 * {@link pnlSign} inspects the characters and returns `-1`, `0`, `1` or `null`, so the outcome
 * filter never converts money to a float — the discipline `paperTradingFormat.js` sets, applied
 * to the one comparison this module needs. `0` is neither a profit nor a loss and matches neither
 * selection; `null` means no P&L was reported, and a trade with no reported P&L is not a loss.
 *
 * WHAT THE COUNTS ARE FOR (Requirement 11.5)
 * -----------------------------------------
 * `totalCount` is the rows before filtering, `resultCount` the rows after. The pair is the only
 * thing that distinguishes Requirement 11.5's two empty states, which are otherwise the same
 * empty table:
 *
 * * `totalCount === 0` → `no-data`. There is nothing to find. Telling this trader to clear a
 *   filter would be pointing at a control that is not the problem.
 * * `resultCount === 0 && totalCount > 0` → `no-match`. The rows exist and a filter is hiding
 *   them, so **this** case — and only this case — offers clear-filters.
 *
 * {@link emptyVariant} is that comparison, and it is deliberately the same one
 * `ds/FilterBar.emptyVariantFor` makes. It is spelled again here rather than imported because
 * `FilterBar.jsx` is a React module and this one must stay importable without React for P18; the
 * test asserts the two agree on every combination, so they cannot drift.
 *
 * HOW `TradeHistory.jsx` CONSUMES THIS (task 15.1 owns the page; this module does not touch it)
 * --------------------------------------------------------------------------------------------
 *     import { DEFAULT_FILTERS, DEFAULT_SEARCH, COUNT_NOUN, filterTrades, tradeRowId }
 *       from './tradeHistoryFilters';
 *
 *     const [filters, setFilters] = useState(DEFAULT_FILTERS);
 *     const [search, setSearch] = useState(DEFAULT_SEARCH);
 *     const view = useMemo(
 *       () => filterTrades(trades, { filters, search, environment }),
 *       [trades, filters, search, environment],
 *     );
 *     const clearFilters = () => { setFilters(DEFAULT_FILTERS); setSearch(DEFAULT_SEARCH); };
 *
 *     <FilterBar
 *       filters={view.controls}                    // the declaration, already environment-aware
 *       values={view.filters}                      // normalised: an unanswerable dimension reads All
 *       onChange={(id, value) => setFilters((prev) => ({ ...prev, [id]: value }))}
 *       search={search} onSearchChange={setSearch}
 *       searchLabel={view.search.label} searchPlaceholder={view.search.placeholder}
 *       resultCount={view.resultCount} totalCount={view.totalCount} countNoun={COUNT_NOUN}
 *       actions={view.hasActiveFilters ? <CommandButton …onClick={clearFilters} /> : null}
 *     />
 *
 *     {view.withheld.map((entry) => (            // why a control the trader may expect is absent
 *       <p key={entry.id}>{entry.label}: {entry.reason}</p>
 *     ))}
 *
 *     {view.emptyVariant ? (
 *       <EmptyState
 *         variant={view.emptyVariant}
 *         clearFiltersAction={view.offersClearFilters
 *           ? { label: 'Clear filters', onClick: clearFilters }
 *           : undefined}
 *         … />
 *     ) : (
 *       <DataTable rows={view.rows} totalCount={view.resultCount} getRowId={tradeRowId} … />
 *     )}
 *
 * Two things there are easy to get backwards. **`FilterBar`'s `totalCount` is the UNFILTERED
 * count and `DataTable`'s is the FILTERED one** — the filter bar explains the table, the table
 * pages over what survived. And `getRowId={tradeRowId}` is not optional: neither real read
 * carries an `id` (the telemetry row has seven columns and none of them is one; a paper row is
 * keyed by `execution_id`), so `DataTable`'s default `row.id ?? index` would key every live row
 * by its position in the array.
 *
 * WHAT PROPERTY 18 (task 15.3, not this task) GENERATES AGAINST
 * -----------------------------------------------------------
 * {@link buildPredicate} is the subject. {@link SIDE_FILTER_VALUES}, {@link OUTCOME_FILTER_VALUES},
 * {@link ALL_VALUE}, {@link ANY_VALUE}, {@link DEFAULT_FILTERS} and {@link ENVIRONMENTS} are the
 * filter-combination space; {@link tradeRowId} is how a row set becomes an id set;
 * {@link filterTrades} is what the page renders, so `filterTrades(...).rows` mapped through
 * `tradeRowId` is the set to compare against `rows.filter(buildPredicate(...))`. {@link marketKey},
 * {@link strategyKey} and {@link pnlSign} are exported so a generator can build rows whose
 * expected membership it knows without re-implementing the readers.
 *
 * @module pages/tradeHistoryFilters
 */

import { PAGES, PAGE_FIELDS_BY_PAGE, VERDICT } from '../design/pageFields';

// ═══════════════════════════════════════════════════════════════════════════
// THE ENVIRONMENT
// ═══════════════════════════════════════════════════════════════════════════

/** The two ledgers this page can show. There is no third: a backtest has no ledger here. */
export const ENVIRONMENTS = Object.freeze({ LIVE: 'LIVE', PAPER: 'PAPER' });

/**
 * An environment label as this module keys it.
 *
 * Anything unrecognised — including `undefined` — is `LIVE`, which is the conservative end: LIVE
 * reports strictly fewer fields, so an unknown environment can only ever offer fewer controls,
 * never a control over a field that read does not carry.
 *
 * @param {string|null|undefined} environment
 * @returns {'LIVE'|'PAPER'}
 */
export function normaliseEnvironment(environment) {
  return String(environment ?? '').trim().toUpperCase() === ENVIRONMENTS.PAPER
    ? ENVIRONMENTS.PAPER
    : ENVIRONMENTS.LIVE;
}

// ═══════════════════════════════════════════════════════════════════════════
// THE FILTER VOCABULARY — DECLARED DATA, FROZEN
// ═══════════════════════════════════════════════════════════════════════════

/** The four dimensions, spelled once. These are `FilterBar`'s `id`s and `values`' keys. */
export const FILTER_IDS = Object.freeze({
  SIDE: 'side',
  OUTCOME: 'outcome',
  MARKET: 'market',
  STRATEGY: 'strategy',
});

/** The segmented filters' "no filter" member — a declared member of a closed vocabulary. */
export const ALL_VALUE = 'ALL';

/**
 * The selects' "nothing selected" value.
 *
 * `''` rather than a second `'ALL'` sentinel, because a market key always carries a `/` and a
 * strategy label is non-empty by construction ({@link strategyKey} drops blanks), so the empty
 * string cannot collide with a real value the way a word can. It is also what a `<select>`
 * natively means by "no choice", which is what `ds/Field` renders.
 */
export const ANY_VALUE = '';

const option = (value, label) => Object.freeze({ value, label });

/** The side filter. `buy`/`sell` is the vocabulary both reads use. */
export const SIDE_FILTERS = Object.freeze([
  option(ALL_VALUE, 'All'),
  option('BUY', 'Buy'),
  option('SELL', 'Sell'),
]);

/** The outcome filter, over reported P&L. Breakeven is in neither Profit nor Loss. */
export const OUTCOME_FILTERS = Object.freeze([
  option(ALL_VALUE, 'All'),
  option('PROFIT', 'Profit'),
  option('LOSS', 'Loss'),
]);

/** The side values, for a caller that iterates the vocabulary rather than re-listing it. */
export const SIDE_FILTER_VALUES = Object.freeze(SIDE_FILTERS.map((o) => o.value));

/** The outcome values. */
export const OUTCOME_FILTER_VALUES = Object.freeze(OUTCOME_FILTERS.map((o) => o.value));

/** Every dimension at its "not filtering" value. The page's initial state, and the reset. */
export const DEFAULT_FILTERS = Object.freeze({
  [FILTER_IDS.SIDE]: ALL_VALUE,
  [FILTER_IDS.OUTCOME]: ALL_VALUE,
  [FILTER_IDS.MARKET]: ANY_VALUE,
  [FILTER_IDS.STRATEGY]: ANY_VALUE,
});

/** The empty query. Named so the page's reset spells it the same way the module does. */
export const DEFAULT_SEARCH = '';

/** What the live region counts, for `FilterBar`'s `countNoun`. */
export const COUNT_NOUN = 'trades';

/** An example, never a label (Requirement 15.1). */
export const SEARCH_PLACEHOLDER = 'BTC/USDT';

/**
 * Which declared `pageFields` field each dimension filters on.
 *
 * This is the join between a control and its availability verdict: without it, "the outcome
 * filter needs P&L" would live only in a comment.
 */
export const FILTER_FIELD = Object.freeze({
  [FILTER_IDS.SIDE]: 'side',
  [FILTER_IDS.OUTCOME]: 'pnl',
  [FILTER_IDS.MARKET]: 'market',
  [FILTER_IDS.STRATEGY]: 'strategy',
});

/** The labels the controls carry. Copy, not derived from the id. */
export const FILTER_LABELS = Object.freeze({
  [FILTER_IDS.SIDE]: 'Side',
  [FILTER_IDS.OUTCOME]: 'Outcome',
  [FILTER_IDS.MARKET]: 'Market',
  [FILTER_IDS.STRATEGY]: 'Strategy',
});

/** Requirement 11.5's two cases, as `ds/EmptyState`'s `EMPTY_VARIANTS` spells them. */
export const EMPTY_VARIANT = Object.freeze({ NO_DATA: 'no-data', NO_MATCH: 'no-match' });

// ═══════════════════════════════════════════════════════════════════════════
// WHAT THE READS ACTUALLY REPORT — TAKEN FROM `design/pageFields.js`
// ═══════════════════════════════════════════════════════════════════════════

const HISTORY_FIELDS = PAGE_FIELDS_BY_PAGE[PAGES.TRADE_HISTORY] ?? [];

/**
 * The Trade History fields `pageFields.js` gives verdict ❌ on the LIVE read.
 *
 * Derived, not restated: today that is `pnl`, `fees`, `slippage` and `strategy`, and if a future
 * backend change makes one of them available the control appears here without an edit.
 *
 * @type {ReadonlyArray<string>}
 */
export const UNREPORTED_ON_LIVE = Object.freeze(
  HISTORY_FIELDS.filter((f) => f.verdict === VERDICT.UNAVAILABLE).map((f) => f.field),
);

/**
 * The three of those four the PAPER read reports, per each entry's own note in `pageFields.js`:
 * `api.paper.getTrades()` rows carry `realized_pnl`, `fee` and a `strategy_id` key.
 *
 * `slippage` is deliberately absent — its entry states that neither the telemetry row nor a paper
 * trade row carries it, so it is unreported in both environments.
 *
 * `strategy` is listed because the key exists, but `PaperTradingService._trade_body` writes
 * `"strategy_id": None` unconditionally, so its VALUE is always `null` today. Declaration alone
 * would therefore offer a select with nothing in it, which is why {@link filterControls} also
 * requires evidence from the rows in hand.
 *
 * @type {ReadonlyArray<string>}
 */
export const PAPER_REPORTED_FIELDS = Object.freeze(['pnl', 'fees', 'strategy']);

/** The `pageFields` entry for one Trade History field, or `null`. */
const historyEntry = (field) => HISTORY_FIELDS.find((f) => f.field === field) ?? null;

/**
 * Whether `environment`'s read reports `field` at all.
 *
 * A field with no ❌ verdict is reported everywhere (`market`, `side`, `time`, `price`,
 * `quantity`, `status`). A field with one is reported only where a note says the paper read
 * carries it.
 *
 * @param {string} field A `pageFields` Trade History field name.
 * @param {string} environment
 * @returns {boolean}
 */
export function isFieldReported(field, environment) {
  if (!UNREPORTED_ON_LIVE.includes(field)) return true;
  return normaliseEnvironment(environment) === ENVIRONMENTS.PAPER
    && PAPER_REPORTED_FIELDS.includes(field);
}

/**
 * The sentence `pageFields.js` gives for a field nothing reports, or `null`.
 *
 * Rendered by the page beside the filter row, so an absent control is explained rather than
 * simply missing (Requirement 19.3's rule, applied to a control instead of a figure).
 *
 * @param {string} field
 * @returns {string|null}
 */
export function fieldAbsenceReason(field) {
  return historyEntry(field)?.reason ?? null;
}

/**
 * Which dimensions the current environment can answer.
 *
 * @param {string} environment
 * @returns {Readonly<Record<string, boolean>>} Keyed by `FILTER_IDS` value.
 */
export function filterAvailability(environment) {
  const env = normaliseEnvironment(environment);
  return Object.freeze(
    Object.fromEntries(
      Object.values(FILTER_IDS).map((id) => [id, isFieldReported(FILTER_FIELD[id], env)]),
    ),
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// READING A ROW — TOTAL, AND NEVER DEFAULTED
// ═══════════════════════════════════════════════════════════════════════════

/** A row is a plain object. Anything else is not a row and is filtered out, not thrown on. */
export const isTradeRow = (row) => Boolean(row) && typeof row === 'object' && !Array.isArray(row);

/** The first key present with a non-blank value, or `null`. */
function readFirst(row, keys) {
  if (!isTradeRow(row)) return null;
  for (const key of keys) {
    const value = row[key];
    if (value === null || value === undefined || typeof value === 'object') continue;
    const text = String(value).trim();
    if (text) return text;
  }
  return null;
}

/**
 * The row's identity, for the memo key, the `<tr key>` and P18's id set.
 *
 * `execution_id` is a paper row's per-fill identity (`order_id` is not — one order has many
 * fills), `id` is the ccxt trade shape's, and the seven-column telemetry row has none of them,
 * which is why the index is the last resort rather than the first choice.
 *
 * @param {Object} row
 * @param {number} index Position in the array as read.
 * @returns {string|number}
 */
export function tradeRowId(row, index) {
  return readFirst(row, ['id', 'execution_id', 'trade_id', 'order_id']) ?? index;
}

/**
 * The market as recorded, or `null`.
 *
 * `symbol` is the real column on both reads; `pair` and `market` are tolerated because today's
 * page reads them and the ccxt fallback is a third-party shape this module does not control.
 */
export const readMarket = (row) => readFirst(row, ['symbol', 'pair', 'market']);

/**
 * One market as a comparison key: upper-cased, with the telemetry path's `_` separator restored
 * to `/`.
 *
 * `executions.symbol` is stored with `/` replaced by `_`, so `BTC_USDT` and `BTC/USDT` are the
 * same market arriving from two reads. Grouping them under one option is the point — two options
 * for one market would split the trader's own ledger in half.
 *
 * @param {string|null|undefined} value
 * @returns {string} `''` when there is no market.
 */
export const marketKey = (value) => String(value ?? '').trim().toUpperCase().replace(/_/g, '/');

/** The strategy label as recorded, or `null`. Never `"Direct"` — see the module docblock. */
export const readStrategy = (row) => readFirst(row, ['strategy', 'strategy_name', 'strategy_id']);

/** One strategy label as a comparison key. Case-folded; blanks are not keys. */
export const strategyKey = (value) => String(value ?? '').trim().toLowerCase();

/**
 * The row's side as `'BUY'`, `'SELL'`, or `null` when it reports none this module recognises.
 *
 * An unrecognised value is `null` rather than folded into one of the two, so a row whose side is
 * some third thing is excluded by both selections instead of being counted as a buy.
 */
export function readSide(row) {
  const raw = readFirst(row, ['side']);
  if (raw === null) return null;
  const upper = raw.toUpperCase();
  return upper === 'BUY' || upper === 'SELL' ? upper : null;
}

/** The P&L as recorded, or `null`. `realized_pnl` is the paper read's column. */
export const readPnl = (row) => readFirst(row, ['pnl', 'realized_pnl', 'realised_pnl', 'profit_loss']);

/** A plain or exponent-notation decimal. Anything else is not a figure. */
const DECIMAL = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

/**
 * The sign of a row's P&L: `1` profit, `-1` loss, `0` breakeven, `null` none reported.
 *
 * Determined from the characters — a leading `-` and whether any mantissa digit is non-zero — so
 * no monetary value is converted to a float to be compared. `-0.00` is breakeven, not a loss.
 *
 * @param {Object} row
 * @returns {-1|0|1|null}
 */
export function pnlSign(row) {
  const text = readPnl(row);
  if (text === null || !DECIMAL.test(text)) return null;
  const digits = text.replace(/^[+-]/, '').split(/[eE]/)[0];
  if (/^[0.]*$/.test(digits)) return 0;
  return text.startsWith('-') ? -1 : 1;
}

// ═══════════════════════════════════════════════════════════════════════════
// THE FILTER STATE
// ═══════════════════════════════════════════════════════════════════════════

/** The search text as compared: trimmed and case-folded. `''` is "not searching". */
export const normaliseQuery = (search) => String(search ?? '').trim().toLowerCase();

const SEPARATORS = /[\s/_-]+/g;

/** `BTC/USDT` -> `btcusdt`, so a query typed without a separator still finds the market. */
const stripSeparators = (text) => text.replace(SEPARATORS, '');

const inVocabulary = (value, vocabulary, fallback) => {
  const upper = String(value ?? '').trim().toUpperCase();
  return vocabulary.includes(upper) ? upper : fallback;
};

/**
 * The filter state as the predicate will apply it.
 *
 * Three things happen here, and each one is a decision the page should not be making twice:
 *
 * 1. A value outside its vocabulary becomes the "no filter" value. A control cannot filter on a
 *    word nothing recognises.
 * 2. A dimension whose field the current environment does not report becomes "no filter" —
 *    dropped, not applied. See the module docblock: applying it would empty a table whose rows
 *    match every filter the trader can actually see.
 * 3. Market and strategy are canonicalised, so the same market spelled two ways is one selection.
 *
 * The page keeps its own raw state, deliberately: switching Live→Paper→Live restores the outcome
 * filter the trader had, because the module decides per call which dimensions are answerable
 * rather than destroying the state on the way past.
 *
 * @param {Object} filters Partial state; missing keys take their default.
 * @param {string} environment
 * @returns {Readonly<{side: string, outcome: string, market: string, strategy: string}>}
 */
export function normaliseFilters(filters, environment) {
  const source = isTradeRow(filters) ? filters : {};
  const available = filterAvailability(environment);
  const side = available[FILTER_IDS.SIDE]
    ? inVocabulary(source[FILTER_IDS.SIDE], SIDE_FILTER_VALUES, ALL_VALUE)
    : ALL_VALUE;
  const outcome = available[FILTER_IDS.OUTCOME]
    ? inVocabulary(source[FILTER_IDS.OUTCOME], OUTCOME_FILTER_VALUES, ALL_VALUE)
    : ALL_VALUE;
  return Object.freeze({
    [FILTER_IDS.SIDE]: side,
    [FILTER_IDS.OUTCOME]: outcome,
    [FILTER_IDS.MARKET]: available[FILTER_IDS.MARKET]
      ? marketKey(source[FILTER_IDS.MARKET])
      : ANY_VALUE,
    [FILTER_IDS.STRATEGY]: available[FILTER_IDS.STRATEGY]
      ? strategyKey(source[FILTER_IDS.STRATEGY])
      : ANY_VALUE,
  });
}

/**
 * How many dimensions are narrowing the table, counting the search as one.
 *
 * The page shows clear-filters on a non-zero count, so a filter that was dropped as unanswerable
 * does not count — offering to clear a filter that is not being applied is noise.
 *
 * @param {Object} filters
 * @param {string} search
 * @param {string} environment
 * @returns {number}
 */
export function activeFilterCount(filters, search, environment) {
  const applied = normaliseFilters(filters, environment);
  const dimensions = Object.values(FILTER_IDS)
    .filter((id) => applied[id] !== DEFAULT_FILTERS[id]).length;
  return dimensions + (normaliseQuery(search) ? 1 : 0);
}

// ═══════════════════════════════════════════════════════════════════════════
// THE FOUR FILTERS, THE SEARCH, AND THE ONE PREDICATE
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Whether a row is on the selected side. A row reporting no side matches `ALL` only.
 *
 * @param {Object} row
 * @param {string} value A `SIDE_FILTER_VALUES` member.
 * @returns {boolean}
 */
export function matchesSide(row, value) {
  const wanted = inVocabulary(value, SIDE_FILTER_VALUES, ALL_VALUE);
  if (wanted === ALL_VALUE) return true;
  return readSide(row) === wanted;
}

/**
 * Whether a row's reported P&L is on the selected side of zero.
 *
 * A row with no reported P&L matches `ALL` only — it is not a loss. Breakeven matches `ALL` only
 * too: `0` is a real reading, and calling it either a profit or a loss would be a claim.
 *
 * @param {Object} row
 * @param {string} value An `OUTCOME_FILTER_VALUES` member.
 * @returns {boolean}
 */
export function matchesOutcome(row, value) {
  const wanted = inVocabulary(value, OUTCOME_FILTER_VALUES, ALL_VALUE);
  if (wanted === ALL_VALUE) return true;
  const sign = pnlSign(row);
  if (sign === null) return false;
  return wanted === 'PROFIT' ? sign > 0 : sign < 0;
}

/**
 * Whether a row is in the selected market. `ANY_VALUE` matches every row.
 *
 * @param {Object} row
 * @param {string} value A market key, or `ANY_VALUE`.
 * @returns {boolean}
 */
export function matchesMarket(row, value) {
  const wanted = marketKey(value);
  if (!wanted) return true;
  return marketKey(readMarket(row)) === wanted;
}

/**
 * Whether a row is from the selected strategy. `ANY_VALUE` matches every row; a row with no
 * strategy label matches nothing else.
 *
 * @param {Object} row
 * @param {string} value A strategy key, or `ANY_VALUE`.
 * @returns {boolean}
 */
export function matchesStrategy(row, value) {
  const wanted = strategyKey(value);
  if (!wanted) return true;
  return strategyKey(readStrategy(row)) === wanted;
}

/**
 * The terms one row is searchable by: its market — in both spellings — and its strategy label
 * when it carries one.
 *
 * Per row rather than per environment on purpose: a row is searchable by what it actually
 * reports, so nothing here depends on a verdict being right about a particular row. The
 * environment decides what the search LABEL claims ({@link searchControl}), which is the part a
 * trader reads.
 *
 * @param {Object} row
 * @returns {string[]} Case-folded.
 */
export function searchTerms(row) {
  const terms = [];
  const market = marketKey(readMarket(row));
  if (market) terms.push(market.toLowerCase());
  const strategy = readStrategy(row);
  if (strategy) terms.push(strategy.trim().toLowerCase());
  return terms;
}

/**
 * Whether a row matches the query. An empty or whitespace-only query matches every row.
 *
 * Substring, case-insensitive, and separator-insensitive on the market: `btcusdt` finds
 * `BTC/USDT`, because the two reads spell the separator differently and a trader should not have
 * to know which one they are looking at.
 *
 * @param {Object} row
 * @param {string} search
 * @returns {boolean}
 */
export function matchesSearch(row, search) {
  const query = normaliseQuery(search);
  if (!query) return true;
  const bare = stripSeparators(query);
  return searchTerms(row).some(
    (term) => term.includes(query) || (Boolean(bare) && stripSeparators(term).includes(bare)),
  );
}

/**
 * The four filters and the search as ONE question, so the page asks it once per row.
 *
 * The subject of Property 18. Deterministic given its arguments, and total: a non-row is `false`
 * rather than a throw.
 *
 * @param {{filters?: Object, search?: string, environment?: string}} [options]
 * @returns {(row: Object) => boolean}
 */
export function buildPredicate(options = {}) {
  const environment = normaliseEnvironment(options.environment);
  const filters = normaliseFilters(options.filters, environment);
  const query = normaliseQuery(options.search);
  return (row) => {
    if (!isTradeRow(row)) return false;
    return matchesSide(row, filters[FILTER_IDS.SIDE])
      && matchesOutcome(row, filters[FILTER_IDS.OUTCOME])
      && matchesMarket(row, filters[FILTER_IDS.MARKET])
      && matchesStrategy(row, filters[FILTER_IDS.STRATEGY])
      && matchesSearch(row, query);
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// THE CONTROLS `FilterBar` RENDERS — DECLARATION PLUS EVIDENCE
// ═══════════════════════════════════════════════════════════════════════════

/** The rows this module will consider. A non-row is in neither count. */
export const tradeRows = (rows) => (Array.isArray(rows) ? rows.filter(isTradeRow) : []);

/**
 * The market select's options: "Any market" plus every market the rows in hand carry, once each,
 * alphabetically.
 *
 * Built from the UNFILTERED rows, so selecting a market does not remove every other market from
 * the control that selected it.
 *
 * @param {Array<Object>} rows
 * @returns {Array<{value: string, label: string}>}
 */
export function marketOptions(rows) {
  const keys = new Set();
  for (const row of tradeRows(rows)) {
    const key = marketKey(readMarket(row));
    if (key) keys.add(key);
  }
  return [
    option(ANY_VALUE, 'Any market'),
    ...[...keys].sort().map((key) => option(key, key)),
  ];
}

/**
 * The strategy select's options: "Any strategy" plus every strategy label the rows carry.
 *
 * The option `value` is the comparison key and the `label` is the first spelling encountered, so
 * two casings of one strategy are one option rather than two.
 *
 * @param {Array<Object>} rows
 * @returns {Array<{value: string, label: string}>}
 */
export function strategyOptions(rows) {
  const labels = new Map();
  for (const row of tradeRows(rows)) {
    const raw = readStrategy(row);
    const key = strategyKey(raw);
    if (key && !labels.has(key)) labels.set(key, raw.trim());
  }
  return [
    option(ANY_VALUE, 'Any strategy'),
    ...[...labels.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([key, label]) => option(key, label)),
  ];
}

/** Why a dimension is not offered. Both causes carry a sentence; neither is silent. */
export const WITHHELD_CAUSES = Object.freeze({
  /** The read does not report the field. `pageFields.js` supplies the reason. */
  UNREPORTED: 'unreported',
  /** The read reports it, but no row in hand carries a value, so there is nothing to filter on. */
  NO_VALUES: 'no-values',
});

/** The `no-values` sentences. Trader-facing: about the ledger, not about a column. */
const NO_VALUE_REASONS = Object.freeze({
  [FILTER_IDS.OUTCOME]:
    'No trade in this ledger reports a P&L, so there is nothing to sort into profit and loss.',
  [FILTER_IDS.MARKET]:
    'This ledger holds trades in fewer than two markets, so there is nothing to choose between.',
  [FILTER_IDS.STRATEGY]: 'No trade in this ledger records a strategy.',
});

/**
 * Every dimension, whether it is offered, and — when it is not — why.
 *
 * `filterControls` and `withheldFilters` are both projections of this one list, which is what
 * makes them exact complements: each of the four dimensions is either a control the trader can
 * use or an absence with a sentence attached. A control that vanished with no explanation reads
 * as a missing feature, and that is the state this function exists to make unreachable.
 *
 * A dimension is offered when its field is reported for the environment AND the rows in hand
 * carry a value for it. Both halves are needed: the declaration catches a column the venue never
 * records (live P&L), and the evidence catches a column that is declared and empty in practice
 * (`_trade_body`'s `strategy_id: null`). The evidence half is also what makes the control appear
 * on its own the day a read starts reporting the field.
 *
 * Side is the exception: its vocabulary is closed rather than derived from the rows, and it is the
 * one filter that means something on an empty ledger, so it is always offered.
 *
 * @param {Array<Object>} rows The unfiltered rows.
 * @param {string} environment
 * @returns {Array<{id: string, label: string, field: string, kind: string, options: Array,
 *   offered: boolean, cause: (string|null), reason: (string|null)}>}
 */
export function filterDecisions(rows, environment) {
  const available = filterAvailability(environment);
  const candidates = tradeRows(rows);
  const markets = marketOptions(candidates);
  const strategies = strategyOptions(candidates);

  const evidence = {
    [FILTER_IDS.SIDE]: true,
    [FILTER_IDS.OUTCOME]: candidates.some((row) => pnlSign(row) !== null),
    // A select whose only option is "Any …" is a dead control.
    [FILTER_IDS.MARKET]: markets.length > 1,
    [FILTER_IDS.STRATEGY]: strategies.length > 1,
  };

  const shape = {
    [FILTER_IDS.SIDE]: { kind: 'segmented', options: SIDE_FILTERS },
    [FILTER_IDS.OUTCOME]: { kind: 'segmented', options: OUTCOME_FILTERS },
    [FILTER_IDS.MARKET]: { kind: 'select', options: markets },
    [FILTER_IDS.STRATEGY]: { kind: 'select', options: strategies },
  };

  return Object.values(FILTER_IDS).map((id) => {
    const field = FILTER_FIELD[id];
    const reported = available[id];
    const offered = reported && evidence[id];
    let cause = null;
    let reason = null;
    if (!reported) {
      cause = WITHHELD_CAUSES.UNREPORTED;
      reason = fieldAbsenceReason(field);
    } else if (!offered) {
      cause = WITHHELD_CAUSES.NO_VALUES;
      reason = NO_VALUE_REASONS[id] ?? null;
    }
    return {
      id,
      label: FILTER_LABELS[id],
      field,
      kind: shape[id].kind,
      options: shape[id].options,
      offered,
      cause,
      reason,
    };
  });
}

/**
 * `FilterBar`'s `filters` prop: the controls this environment and these rows can support, in a
 * fixed order, so the row does not reorder when a dimension drops out.
 *
 * @param {Array<Object>} rows The unfiltered rows.
 * @param {string} environment
 * @returns {Array<{id: string, label: string, kind: string, options: Array}>}
 */
export function filterControls(rows, environment) {
  return filterDecisions(rows, environment)
    .filter((decision) => decision.offered)
    .map(({ id, label, kind, options }) => ({ id, label, kind, options }));
}

/**
 * The dimensions that are not offered, each with a sentence saying why.
 *
 * The page renders these beside the filter row. `cause` lets it word the two differently: an
 * `unreported` dimension is the ledger being honest about what the venue records (Requirement
 * 19.3's rule applied to a control rather than a figure), while `no-values` is simply nothing to
 * choose from yet.
 *
 * @param {Array<Object>} rows
 * @param {string} environment
 * @returns {Array<{id: string, label: string, field: string, cause: string, reason: (string|null)}>}
 */
export function withheldFilters(rows, environment) {
  return filterDecisions(rows, environment)
    .filter((decision) => !decision.offered)
    .map(({ id, label, field, cause, reason }) => ({ id, label, field, cause, reason }));
}

/**
 * The search control's copy: a visible label naming what is searched, and an example.
 *
 * The label narrows to "Search market" when no strategy is searchable, because "Search market or
 * strategy" over a ledger that records no strategy promises something the data cannot do.
 *
 * @param {Array<Object>} rows
 * @param {string} environment
 * @returns {{label: string, placeholder: string, fields: string[]}}
 */
export function searchControl(rows, environment) {
  const strategySearchable = isFieldReported('strategy', environment)
    && tradeRows(rows).some((row) => readStrategy(row) !== null);
  return {
    label: strategySearchable ? 'Search market or strategy' : 'Search market',
    placeholder: SEARCH_PLACEHOLDER,
    fields: strategySearchable ? ['market', 'strategy'] : ['market'],
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// THE COUNTS AND REQUIREMENT 11.5'S VARIANT DECISION
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Which empty state is due, or `null` when the table has rows.
 *
 * The same comparison as `ds/FilterBar.emptyVariantFor`, spelled here so this module needs no
 * React (see the module docblock). The test asserts the two agree.
 *
 * @param {number} resultCount Rows after filtering.
 * @param {number} totalCount Rows before filtering.
 * @returns {'no-data'|'no-match'|null}
 */
export function emptyVariant(resultCount, totalCount) {
  const result = Number.isFinite(resultCount) ? resultCount : 0;
  const total = Number.isFinite(totalCount) ? totalCount : 0;
  if (result > 0) return null;
  return total > 0 ? EMPTY_VARIANT.NO_MATCH : EMPTY_VARIANT.NO_DATA;
}

/**
 * Whether the empty state offers clear-filters. `no-match` only.
 *
 * `no-data` must not: there is no filter in the way, and `ds/EmptyState` would then lead with a
 * control that changes nothing. `no-match` REQUIRES it — the component asserts as much — because
 * the rows exist and widening the filter is the way to them (Requirement 11.5).
 *
 * @param {string|null} variant
 * @returns {boolean}
 */
export const offersClearFilters = (variant) => variant === EMPTY_VARIANT.NO_MATCH;

/**
 * Everything the page renders from, in one call.
 *
 * `totalCount` counts the rows before filtering and `resultCount` after, over the same set — the
 * rows this module accepted as rows — so `resultCount <= totalCount` holds, which is what
 * `FilterBar` asserts and what {@link emptyVariant} needs to be meaningful.
 *
 * @param {Array<Object>} rows As read from `api.orders.getHistory()` / `api.paper.getTrades()`.
 * @param {{filters?: Object, search?: string, environment?: string}} [options]
 * @returns {{
 *   environment: string, filters: Object, query: string, rows: Object[],
 *   totalCount: number, resultCount: number, emptyVariant: (string|null),
 *   offersClearFilters: boolean, activeFilterCount: number, hasActiveFilters: boolean,
 *   controls: Object[], withheld: Object[],
 *   search: {label: string, placeholder: string, fields: string[]},
 *   marketOptions: Object[], strategyOptions: Object[]
 * }}
 */
export function filterTrades(rows, options = {}) {
  const environment = normaliseEnvironment(options.environment);
  const filters = normaliseFilters(options.filters, environment);
  const query = normaliseQuery(options.search);
  const candidates = tradeRows(rows);
  const matched = candidates.filter(buildPredicate({ filters, search: query, environment }));
  const totalCount = candidates.length;
  const resultCount = matched.length;
  const variant = emptyVariant(resultCount, totalCount);
  const active = activeFilterCount(filters, query, environment);

  return {
    environment,
    filters,
    // The compared form, not what was typed: the page owns the input's value.
    query,
    rows: matched,
    totalCount,
    resultCount,
    emptyVariant: variant,
    offersClearFilters: offersClearFilters(variant),
    activeFilterCount: active,
    hasActiveFilters: active > 0,
    controls: filterControls(candidates, environment),
    withheld: withheldFilters(candidates, environment),
    search: searchControl(candidates, environment),
    marketOptions: marketOptions(candidates),
    strategyOptions: strategyOptions(candidates),
  };
}
