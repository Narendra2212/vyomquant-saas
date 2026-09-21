/**
 * ═══════════════════════════════════════════════════════════════════════════
 * pages/TradeHistory — the ledger (`/app/trades`)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 15.1. design.md §7.7. Requirements 11.1, 11.3, 11.4, 11.6,
 * 12.2, 15.4, 17.2.
 *
 * Three modules landed before this page and it renders through all three rather than
 * deciding anything they already decide:
 *
 *   * `pages/tradeHistoryFilters.js` (15.2) — every "which rows am I looking at" rule,
 *     plus the counts and the empty-state variant. One call, {@link filterTrades}, and
 *     the page renders its answer.
 *   * `design/pageFields.js` (13.3) — which columns the read actually reports, and the
 *     sentence a trader reads instead of a figure when it does not. Four of this page's
 *     columns are ❌ on the live read (`pnl`, `fees`, `slippage`, `strategy`) because the
 *     `executions` telemetry row has seven columns and carries none of them.
 *   * `components/ds/*` — `DataTable`, `FilterBar`, `EmptyState`, `Panel`, `Metric`,
 *     `PageHeader`, `TradingEnvironmentBadge`. Imported by path, not through the barrel,
 *     so `ds/Chart`'s recharts dependency stays out of this page's import graph.
 *
 * WHAT THE FOUR SUMMARY FIGURES SHOW, AND WHY (Requirement 14.5)
 * -------------------------------------------------------------
 * §7.7 asks for Trades / Win rate / Total P&L / Total fees, and three of those four derive
 * from fields the live read does not report. So on **Live**:
 *
 *   * **Trades** is real — it is the number of rows the read returned.
 *   * **Win rate**, **Total P&L** and **Total fees** render the not-available marker
 *     carrying `pageFields`' own reason. `log_execution` writes `(timestamp, user_id,
 *     symbol, side, status, amount, price)` and nothing else, so there is no per-trade P&L
 *     to sign and no fee to add up. The page this replaces rendered `$0.00` for total fees
 *     on every live account, which is a claim that the venue charged nothing.
 *
 * On **Paper** all four are figures, because `api.paper.getTrades()` rows carry
 * `realized_pnl` and `fee` for real — and even then a total is only stated when EVERY row
 * in the ledger reports the field. A ledger where three of ten trades report a fee has a
 * knowable sum of three fees and an unknowable total, so it renders the marker and says
 * how many rows are missing. Sums are computed on the digits ({@link sumDecimals}), never
 * by adding floats: `realized_pnl` is `NUMERIC(28,10)` on the wire.
 *
 * A read that returned no rows at all makes all four markers, Trades included. On the live
 * side `[]` genuinely means both "no trades" and "the telemetry read failed and no
 * `exchange_id` was available to fall back on" (§7.7), so a `0` there would be a count of
 * something nobody read.
 *
 * THE COLUMNS (Requirement 11.1)
 * ------------------------------
 * `Time Market Side Qty Price P&L Fees Slippage Strategy Status` — ten, from §7.7's sketch.
 * Against today's twelve:
 *
 *   * `status` is **added**. Requirement 11.1 names it, it is a real telemetry column, and
 *     the table this replaces omitted it.
 *   * `#` is **dropped**. A row index is not information.
 *   * `Venue` is **dropped**. Neither read reports an exchange, and the page it replaces
 *     defaulted the cell to `"binance"` on live and `"paper"` on paper.
 *   * `Entry` + `Exit` **collapse to one `Price`**. There is one price per row; rendering
 *     two columns from `row.price` made every fill look like a round trip at one number.
 *   * Quantity, price, P&L, fees and slippage are `align: 'numeric'` (Requirement 11.3).
 *     Every column was `textAlign: "left"` before this.
 *
 * Fees, slippage and strategy are `priority: 3`, so below `--breakpoint-laptop` they leave
 * the row and reappear in `DataTable`'s per-row expander (Requirement 17.2). The reduction
 * is CSS, so the DOM is the same at every width.
 *
 * AVAILABILITY IS READ FROM THE DECLARATION, NOT FROM THE ROW
 * ----------------------------------------------------------
 * A column the current environment does not report renders the marker for every row, with
 * `pageFields`' reason attached — not "whatever the row happened to carry under that key".
 * That is deliberate and it is the same rule `tradeHistoryFilters` applies to the filter
 * controls: the ccxt fallback shape and a hand-written fixture can both carry a `pnl`-ish
 * key that the live ledger does not, and rendering it would put a figure on screen that
 * the account's own executions do not record. `isFieldReported` is the single gate.
 *
 * WHAT REPLACED THE DUPLICATE `SimulatedIndicator`
 * -----------------------------------------------
 * `ds/TradingEnvironmentBadge`, which is where that component's copy now lives verbatim —
 * `SIMULATED · SERVER LABEL UNAVAILABLE`, reached on exactly the same condition (the
 * server flagged the figures simulated and named no environment). It is read from the
 * `GET /api/paper/trades` envelope's own `execution_environment` / `is_simulated` and from
 * nothing else: not from the switch below, not from the route. The full-width `strip`
 * variant sits under the header and announces once; the `chip` on each `Panel` marks the
 * regions holding the figures it qualifies (Requirement 12.2), which is where the page it
 * replaces already put it and the reason it put it there.
 *
 * PAGINATION AND THE TABLE'S WIDTH
 * --------------------------------
 * 50 rows a page (Requirement 11.4), client-side: `api.paper.getTrades(100)` and
 * `GET /api/orders/history` both answer one batch, so `DataTable` holds the whole filtered
 * set and slices it. `stickyHeader` keeps the header on screen inside that. The
 * `minWidth: 780`-inside-`overflowX: auto` pattern this page used to write by hand is what
 * `DataTable` generalises — its wrapper is the `overflow-x-auto` one, with
 * `scrollbar-gutter: stable`, and the min-width lives on the `<table>` — so the remaining
 * columns scroll rather than being clipped at tablet width.
 *
 * @module pages/TradeHistory
 */

import { useCallback, useId, useMemo, useState } from 'react';
import { Download, Receipt, RefreshCw } from 'lucide-react';

import { api } from '../api';
import { CommandButton } from '../components/ds/CommandButton';
import { DataTable } from '../components/ds/DataTable';
import { EmptyState } from '../components/ds/EmptyState';
import { FilterBar } from '../components/ds/FilterBar';
import { Metric, NotAvailableMarker } from '../components/ds/Metric';
import { PageHeader } from '../components/ds/PageHeader';
import { Panel } from '../components/ds/Panel';
import { PnLDisplay } from '../components/ds/PnLDisplay';
import { StatusBadge } from '../components/ds/StatusBadge';
import { TradingEnvironmentBadge } from '../components/ds/TradingEnvironmentBadge';
import { PAGES, PAGE_FIELDS_BY_PAGE } from '../design/pageFields';
import { available, unavailable } from '../design/reported';
import { PANEL_STATES, usePanelState } from '../hooks/usePanelState';
import { formatQuantity, renderDecimalParts, splitDecimalText } from './paperTradingFormat';
import {
  COUNT_NOUN,
  DEFAULT_FILTERS,
  DEFAULT_SEARCH,
  ENVIRONMENTS,
  filterTrades,
  isFieldReported,
  marketKey,
  pnlSign,
  readMarket,
  readPnl,
  readSide,
  readStrategy,
  tradeRowId,
  tradeRows,
} from './tradeHistoryFilters';

/* ══════════════════════════════════════════════════════════════════════════
 * THE DECLARATION — labels and reasons come from `design/pageFields.js`
 * ══════════════════════════════════════════════════════════════════════════ */

const HISTORY_FIELDS = PAGE_FIELDS_BY_PAGE[PAGES.TRADE_HISTORY] ?? [];

/** One field's declaration, or `null`. */
const fieldEntry = (field) => HISTORY_FIELDS.find((entry) => entry.field === field) ?? null;

/** The column header §7.7 names for a field. Read, not restated. */
const labelFor = (field) => fieldEntry(field)?.label ?? field;

/**
 * The sentence rendered instead of a figure, from the declaration.
 *
 * `undefined` rather than `null` when a field declares none, so `NotAvailableMarker` falls
 * back to its own `UNREPORTED_REASON` instead of being handed an empty reason.
 */
const reasonFor = (field) => fieldEntry(field)?.reason ?? undefined;

/** `api.paper.getTrades`'s batch size, as §7.7 fixes it. */
const PAPER_TRADE_LIMIT = 100;

/** Requirement 11.4 / §7.7, via `DataTable`'s own default. */
const PAGE_SIZE = 50;

/** Newest first: a ledger is read from the most recent fill backwards. */
const DEFAULT_SORT = Object.freeze({ key: 'time', direction: 'desc' });

/** The two ledgers, in the order the switch offers them. */
const LEDGERS = Object.freeze([
  Object.freeze({ value: ENVIRONMENTS.LIVE, label: 'Live' }),
  Object.freeze({ value: ENVIRONMENTS.PAPER, label: 'Paper' }),
]);

/** The columns the CSV carries, in the table's own order. */
const CSV_FIELDS = Object.freeze([
  'time',
  'market',
  'side',
  'quantity',
  'price',
  'pnl',
  'fees',
  'slippage',
  'strategy',
  'status',
]);

/*
 * Which keys each cell reads, per read. Both reads are covered by one list because a row
 * carries one spelling or the other and never both: `executions` writes `timestamp` and
 * `amount`, `PaperTradingService._trade_body` writes `executed_at` and `quantity`, and the
 * ccxt fallback shape adds `datetime`. Nothing here is a default — a row with none of the
 * keys yields `null` and renders the marker.
 */
const TIME_KEYS = Object.freeze(['timestamp', 'executed_at', 'datetime']);
const QUANTITY_KEYS = Object.freeze(['amount', 'quantity']);
const PRICE_KEYS = Object.freeze(['price']);
const FEE_KEYS = Object.freeze(['fee']);
const STATUS_KEYS = Object.freeze(['status', 'order_lifecycle_state']);

/* ══════════════════════════════════════════════════════════════════════════
 * READING A ROW
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The first of `keys` the row carries a scalar for, exactly as it arrived.
 *
 * Values are **not** stringified: a ccxt `timestamp` is epoch milliseconds as a number and
 * a paper `price` is an exact decimal string, and both have to reach `DataTable`'s
 * comparator in the form they came in.
 *
 * @param {Object} row
 * @param {ReadonlyArray<string>} keys
 * @returns {string|number|boolean|null}
 */
function firstValue(row, keys) {
  if (!row || typeof row !== 'object') return null;
  for (const key of keys) {
    const value = row[key];
    if (value === null || value === undefined || typeof value === 'object') continue;
    if (typeof value === 'string' && value.trim() === '') continue;
    return value;
  }
  return null;
}

/** The fee as recorded, or `null`. `fee` is the paper read's column; live records none. */
const readFee = (row) => firstValue(row, FEE_KEYS);

/**
 * One raw row as the ten cells the table reads.
 *
 * `id` is {@link tradeRowId}'s answer for the raw row, so `getRowId={tradeRowId}` on the
 * projected row returns the same identity — neither read carries an `id` of its own (a
 * telemetry row has seven columns and none of them is one), which is why the prop is not
 * optional.
 *
 * @param {Object} row
 * @param {number} index Position in the filtered set.
 */
function projectTrade(row, index) {
  return {
    id: tradeRowId(row, index),
    time: firstValue(row, TIME_KEYS),
    // `executions.symbol` stores `BTC_USDT`; display formatting is this page's job.
    market: marketKey(readMarket(row)) || null,
    side: readSide(row),
    quantity: firstValue(row, QUANTITY_KEYS),
    price: firstValue(row, PRICE_KEYS),
    pnl: readPnl(row),
    fees: readFee(row),
    // Nothing reports slippage in either environment: it needs an intended price to
    // compare the fill against and no read carries one. The column renders its reason.
    slippage: null,
    strategy: readStrategy(row),
    status: firstValue(row, STATUS_KEYS),
  };
}

/* ══════════════════════════════════════════════════════════════════════════
 * EXACT SUMS — on the digits, never through a float
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * Add decimal strings exactly, and return one plain decimal string.
 *
 * Every value is scaled to the widest fraction in the set and added as a `BigInt`, so a
 * ledger of `NUMERIC(28,10)` figures totals to the digit. The result is **ungrouped** —
 * `ds/Metric` does the grouping, and its formatter refuses a string with separators in it.
 * Trailing zeros beyond two places are dropped, which shortens `12.5000000000` to `12.50`
 * without rounding anything: no digit that carries information is removed.
 *
 * @param {ReadonlyArray<{negative: boolean, whole: string, fraction: string}>} parts
 * @returns {string}
 */
function sumDecimals(parts) {
  const scale = parts.reduce((widest, part) => Math.max(widest, part.fraction.length), 0);
  let total = 0n;
  for (const part of parts) {
    const digits = `${part.whole}${part.fraction.padEnd(scale, '0')}`;
    const magnitude = BigInt(digits === '' ? '0' : digits);
    total += part.negative ? -magnitude : magnitude;
  }

  const negative = total < 0n;
  const digits = (negative ? -total : total).toString().padStart(scale + 1, '0');
  const whole = digits.slice(0, digits.length - scale);
  const fraction = scale === 0 ? '' : digits.slice(digits.length - scale);
  let shown = fraction.replace(/0+$/, '');
  if (shown.length < 2) shown = shown.padEnd(2, '0');
  const body = `${whole}.${shown}`;
  return negative && !/^[0.]*$/.test(body) ? `-${body}` : body;
}

/**
 * The ledger's total for one figure, as a `Reported<T>`.
 *
 * Stated only when every row reports the figure. A partial ledger has a knowable sum of
 * the rows that reported and an unknowable total, and the difference between those two is
 * exactly what a trader reading "Total fees" would get wrong.
 *
 * @param {Array<Object>} rows The whole ledger, unfiltered.
 * @param {(row: Object) => (string|number|null)} read
 * @param {string} noun What is being totalled, for the reason.
 */
function totalOf(rows, read, noun) {
  const parts = [];
  let missing = 0;
  let unreadable = 0;

  for (const row of rows) {
    const value = read(row);
    if (value === null || value === undefined) {
      missing += 1;
      continue;
    }
    const part = splitDecimalText(value);
    if (part === null) unreadable += 1;
    else parts.push(part);
  }

  if (unreadable > 0) {
    return unavailable(
      `${unreadable} of ${rows.length} trades report a ${noun} this page could not read as a `
        + 'figure, so no total can be stated.',
    );
  }
  if (missing > 0) {
    return unavailable(
      `${missing} of ${rows.length} trades report no ${noun}, so no total can be stated.`,
    );
  }
  return available(sumDecimals(parts));
}

/* ══════════════════════════════════════════════════════════════════════════
 * CELLS
 * ══════════════════════════════════════════════════════════════════════════ */

/** A side, as a status chip. `buy` and `sell` are `semantic.js`'s own vocabulary. */
function SideCell({ value }) {
  if (typeof value !== 'string' || value.trim() === '') {
    return <NotAvailableMarker label={labelFor('side')} reason={reasonFor('side')} />;
  }
  return <StatusBadge state={value} />;
}

/** A quantity, exactly as recorded — no padding, no rounding. */
function QuantityCell({ value }) {
  const text = formatQuantity(value);
  return text === null
    ? <NotAvailableMarker label={labelFor('quantity')} reason={reasonFor('quantity')} />
    : text;
}

/** A price, to at least two places, every further digit preserved. */
function PriceCell({ value }) {
  const parts = splitDecimalText(value);
  return parts === null
    ? <NotAvailableMarker label={labelFor('price')} reason={reasonFor('price')} />
    : renderDecimalParts(parts, 2);
}

/** The server's status, verbatim, through `statusToken`. The column §7.7 adds. */
function StatusCell({ value }) {
  if (typeof value !== 'string' || value.trim() === '') {
    return <NotAvailableMarker label={labelFor('status')} reason={reasonFor('status')} />;
  }
  return <StatusBadge state={value} />;
}

/** A recorded fee. Reached only where the environment reports one. */
function FeeCell({ value }) {
  const parts = splitDecimalText(value);
  return parts === null
    ? <NotAvailableMarker label={labelFor('fees')} />
    : renderDecimalParts(parts, 2);
}

/** A recorded strategy label. Reached only where the environment reports one. */
function StrategyCell({ value }) {
  return typeof value === 'string' && value.trim() !== ''
    ? value
    : <NotAvailableMarker label={labelFor('strategy')} />;
}

/** The marker a column renders for every row when the read does not report the field. */
function unreportedCell(field) {
  const label = labelFor(field);
  const reason = reasonFor(field);
  return function UnreportedCell() {
    return <NotAvailableMarker label={label} reason={reason} />;
  };
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE LEDGER SWITCH
 * ══════════════════════════════════════════════════════════════════════════ */

const LEDGER_CHIP_CLASSES =
  'inline-flex cursor-pointer items-center rounded-sm border border-line-default px-2 py-0.5 '
  + 'text-micro font-mono font-bold uppercase tracking-wide text-content-secondary '
  + 'transition-colors hover:border-line-strong hover:text-content-primary '
  + 'peer-checked:border-brand peer-checked:bg-brand-wash peer-checked:text-brand '
  // The radio is `sr-only`, so the focus ring is drawn on the chip the trader can see.
  + 'peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 '
  + 'peer-focus-visible:outline-brand';

/**
 * Live / Paper, as native radios in a labelled group.
 *
 * `ds/FilterBar`'s segmented control, applied to the header: a `<fieldset>` with a
 * `<legend>` and two `<input type="radio">`s, which gets arrow-key movement, single
 * selection, one tab stop and a real label association from the browser rather than from a
 * `role="radiogroup"` reimplementation. Two `<button>`s would also have made the selected
 * one a control that does nothing when pressed.
 */
function LedgerSwitch({ value, onChange }) {
  const groupId = useId();

  return (
    <fieldset className="flex min-w-0 flex-col gap-1 border-0 p-0">
      <legend className="p-0 text-micro font-medium uppercase tracking-wider text-content-secondary">
        Ledger
      </legend>
      <div className="flex items-center gap-1">
        {LEDGERS.map((ledger) => {
          const optionId = `${groupId}-${ledger.value}`;
          return (
            <div key={ledger.value} className="relative">
              <input
                type="radio"
                id={optionId}
                name={`${groupId}-ledger`}
                value={ledger.value}
                checked={value === ledger.value}
                onChange={() => onChange(ledger.value)}
                className="peer sr-only"
              />
              <label htmlFor={optionId} className={LEDGER_CHIP_CLASSES}>
                {ledger.label}
              </label>
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE PAGE
 * ══════════════════════════════════════════════════════════════════════════ */

const NO_ROWS_REASON =
  'No trades were read for this ledger, so there is nothing to compute a summary from.';

const NO_PNL_REASON =
  'No trade in this ledger reports a P&L, so no win rate can be computed.';

/** `"` is doubled and every field is quoted, so a symbol or a label cannot break the row. */
const escapeCsv = (value) => `"${String(value ?? '').replace(/"/g, '""')}"`;

export default function TradeHistory() {
  const [environment, setEnvironment] = useState(ENVIRONMENTS.LIVE);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [search, setSearch] = useState(DEFAULT_SEARCH);
  const [sort, setSort] = useState(DEFAULT_SORT);
  const [page, setPage] = useState(1);

  const isPaper = environment === ENVIRONMENTS.PAPER;

  // One read per ledger, and the environment is the question: switching it discards the
  // previous answer rather than filtering it, because a live row and a paper row are not
  // the same ledger.
  const reader = useCallback(
    () => (isPaper ? api.paper.getTrades(PAPER_TRADE_LIMIT) : api.orders.getHistory()),
    [isPaper],
  );
  const { state, data, error, refetch } = usePanelState(reader, { deps: [environment] });

  /*
   * `GET /api/orders/history` answers a bare array; `GET /api/paper/trades` answers
   * `{trades, count, execution_environment, is_simulated, session_id}`. Those are the two
   * shapes, and no third is guessed at.
   */
  const rawRows = useMemo(() => {
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.trades)) return data.trades;
    return [];
  }, [data]);

  // Read off the envelope and nothing else — never from the switch above (§8.1). A body
  // carrying neither field leaves both as they are, and the badge says so.
  const paperEnvironment = isPaper && typeof data?.execution_environment === 'string'
    && data.execution_environment.trim() !== ''
    ? data.execution_environment
    : null;
  const paperSimulated = isPaper && data?.is_simulated === true;

  const view = useMemo(
    () => filterTrades(rawRows, { filters, search, environment }),
    [rawRows, filters, search, environment],
  );

  const ledger = useMemo(() => tradeRows(rawRows), [rawRows]);

  const displayRows = useMemo(() => view.rows.map(projectTrade), [view.rows]);

  const pnlReported = isFieldReported('pnl', environment);
  const feesReported = isFieldReported('fees', environment);
  const strategyReported = isFieldReported('strategy', environment);

  /* ── The summary (Requirement 14.5) ─────────────────────────────────── */

  const summary = useMemo(() => {
    if (ledger.length === 0) {
      const none = unavailable(NO_ROWS_REASON);
      return { trades: none, winRate: none, totalPnl: none, totalFees: none };
    }

    const signs = ledger.map(pnlSign).filter((sign) => sign !== null);
    const winRate = !pnlReported
      ? unavailable(reasonFor('pnl'))
      : signs.length === 0
        ? unavailable(NO_PNL_REASON)
        // A rate over two integer counts. Not money, so no exact-decimal handling is owed.
        : available((signs.filter((sign) => sign > 0).length / signs.length) * 100);

    return {
      trades: available(ledger.length),
      winRate,
      totalPnl: pnlReported
        ? totalOf(ledger, readPnl, 'P&L')
        : unavailable(reasonFor('pnl')),
      totalFees: feesReported
        ? totalOf(ledger, readFee, 'fee')
        : unavailable(reasonFor('fees')),
    };
  }, [ledger, pnlReported, feesReported]);

  /* ── Columns (Requirements 11.1, 11.3, 17.2) ────────────────────────── */

  const columns = useMemo(() => [
    { key: 'time', header: labelFor('time'), format: 'timestamp', sortable: true, priority: 1 },
    { key: 'market', header: labelFor('market'), format: 'symbol', sortable: true, priority: 1 },
    { key: 'side', header: labelFor('side'), render: SideCell, priority: 1 },
    {
      key: 'quantity',
      header: labelFor('quantity'),
      align: 'numeric',
      sortable: true,
      render: QuantityCell,
      priority: 2,
    },
    {
      key: 'price',
      header: labelFor('price'),
      align: 'numeric',
      format: 'currency',
      sortable: true,
      render: PriceCell,
      priority: 1,
    },
    {
      key: 'pnl',
      header: labelFor('pnl'),
      align: 'numeric',
      // A dead sort header on a column of markers is a control that does nothing.
      sortable: pnlReported,
      render: pnlReported
        ? PnLDisplay
        : unreportedCell('pnl'),
      priority: 2,
    },
    {
      key: 'fees',
      header: labelFor('fees'),
      align: 'numeric',
      format: 'currency',
      sortable: feesReported,
      render: feesReported ? FeeCell : unreportedCell('fees'),
      priority: 3,
    },
    {
      key: 'slippage',
      header: labelFor('slippage'),
      align: 'numeric',
      render: unreportedCell('slippage'),
      priority: 3,
    },
    {
      key: 'strategy',
      header: labelFor('strategy'),
      render: strategyReported ? StrategyCell : unreportedCell('strategy'),
      priority: 3,
    },
    { key: 'status', header: labelFor('status'), render: StatusCell, priority: 2 },
  ], [pnlReported, feesReported, strategyReported]);

  /* ── Handlers. Every one that changes the row set returns to page 1, or a
   *    stale page number would render an empty table over rows that exist. ── */

  const handleLedgerChange = useCallback((next) => {
    setEnvironment(next);
    setPage(1);
  }, []);

  const handleFilterChange = useCallback((id, value) => {
    setFilters((previous) => ({ ...previous, [id]: value }));
    setPage(1);
  }, []);

  const handleSearchChange = useCallback((text) => {
    setSearch(text);
    setPage(1);
  }, []);

  const clearFilters = useCallback(() => {
    setFilters(DEFAULT_FILTERS);
    setSearch(DEFAULT_SEARCH);
    setPage(1);
  }, []);

  /* ── Export (§7.7's header action) ──────────────────────────────────── */

  const exportCsv = useCallback(() => {
    if (displayRows.length === 0) return;

    const reported = {
      pnl: pnlReported,
      fees: feesReported,
      strategy: strategyReported,
      slippage: false,
    };
    const lines = [
      CSV_FIELDS.map((field) => labelFor(field)),
      ...displayRows.map((row) =>
        CSV_FIELDS.map((field) => (reported[field] === false ? '' : row[field]))),
    ].map((cells) => cells.map(escapeCsv).join(','));

    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `trade-history-${environment.toLowerCase()}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }, [displayRows, environment, pnlReported, feesReported, strategyReported]);

  /* ── Empty states (Requirement 11.5) ───────────────────────────────── */

  const noDataState = isPaper
    ? {
      icon: Receipt,
      headline: 'No paper trades recorded',
      body: 'A paper session records a trade each time one of its simulated orders fills. '
        + 'Start or resume a session and this ledger fills as it runs.',
      action: { label: 'Open Paper Trading', to: '/app/paper-trading' },
    }
    : {
      icon: Receipt,
      headline: 'No trades recorded',
      body: 'This ledger fills as your deployed strategies execute. Manual orders are '
        + 'refused by the platform, so every row here comes from a strategy.',
      action: { label: 'Review your strategies', to: '/app/strategies' },
    };

  const busy = state === PANEL_STATES.LOADING || state === PANEL_STATES.REFRESHING;

  return (
    <div className="flex min-w-0 flex-col gap-4 overflow-y-auto bg-surface-canvas p-5 text-content-primary">
      <PageHeader
        title="Trade History"
        meta={<LedgerSwitch value={environment} onChange={handleLedgerChange} />}
        actions={(
          <>
            <CommandButton
              intent="secondary"
              icon={RefreshCw}
              loading={busy}
              loadingLabel="Reading ledger"
              onClick={refetch}
            >
              Refresh
            </CommandButton>
            <CommandButton
              intent="secondary"
              icon={Download}
              onClick={exportCsv}
              disabled={displayRows.length === 0}
              // Kept to one short line: `CommandButton` renders the reason as visible
              // text beside the control, inside `PageHeader`'s fixed-height block.
              disabledReason="No trades in view to export."
            >
              Export CSV
            </CommandButton>
          </>
        )}
      />

      {/* Requirement 12.2 / §7.7. Announced once, here, rather than on each chip below. */}
      {isPaper ? (
        <TradingEnvironmentBadge
          environment={paperEnvironment}
          isSimulated={paperSimulated}
          variant="strip"
          announce
        />
      ) : null}

      <Panel
        title="Ledger summary"
        money
        environment={isPaper ? paperEnvironment : ENVIRONMENTS.LIVE}
        state={state === PANEL_STATES.LOADING ? PANEL_STATES.LOADING : PANEL_STATES.READY}
        loading={{ kind: 'skeleton-metric', rows: 1, columns: 4 }}
      >
        {/* Four tracks at every width. `minmax(0, 1fr)` shrinks rather than clipping, so
            the row needs no breakpoint variant to survive tablet width (Req 17.2). */}
        <div className="grid grid-cols-4 gap-4">
          <Metric label="Trades" value={summary.trades} format="integer" />
          <Metric label="Win rate" value={summary.winRate} format="percent" precision={1} />
          <Metric label="Total P&L" value={summary.totalPnl} format="currency" />
          <Metric label="Total fees" value={summary.totalFees} format="currency" />
        </div>
      </Panel>

      <Panel
        title="Trades"
        money
        environment={isPaper ? paperEnvironment : ENVIRONMENTS.LIVE}
        state={state}
        loading={{ kind: 'skeleton-table', rows: 8, columns: columns.length }}
        empty={noDataState}
        error={{ error, context: 'trades', onRetry: refetch }}
      >
        <div className="flex min-w-0 flex-col gap-3">
          <FilterBar
            filters={view.controls}
            values={view.filters}
            onChange={handleFilterChange}
            search={search}
            onSearchChange={handleSearchChange}
            searchLabel={view.search.label}
            searchPlaceholder={view.search.placeholder}
            resultCount={view.resultCount}
            totalCount={view.totalCount}
            countNoun={COUNT_NOUN}
            actions={view.hasActiveFilters ? (
              <CommandButton intent="ghost" onClick={clearFilters}>
                Clear filters
              </CommandButton>
            ) : null}
          />

          {/* A control that is not offered is explained rather than simply absent —
              Requirement 19.3's rule, applied to a filter instead of a figure. No
              `list-none` on the list: the preflight reset already drops the marker and
              the padding, and a class that compiles to nothing is what §1.2 is about. */}
          {view.withheld.length > 0 ? (
            <ul className="flex flex-col gap-1 text-micro text-content-secondary">
              {view.withheld.map((entry) => (
                <li key={entry.id}>
                  <span className="font-medium text-content-primary">{`${entry.label}: `}</span>
                  {entry.reason}
                </li>
              ))}
            </ul>
          ) : null}

          {view.emptyVariant ? (
            <EmptyState
              {...noDataState}
              variant={view.emptyVariant}
              {...(view.offersClearFilters
                ? {
                  headline: 'No trades match these filters',
                  body: `This ledger holds ${view.totalCount} ${COUNT_NOUN}, and none of them `
                    + 'matches the current filters and search. Widen them to see the rows again.',
                  clearFiltersAction: { label: 'Clear filters', onClick: clearFilters },
                }
                : null)}
            />
          ) : (
            <DataTable
              columns={columns}
              rows={displayRows}
              getRowId={tradeRowId}
              totalCount={view.resultCount}
              page={page}
              pageSize={PAGE_SIZE}
              onPageChange={setPage}
              sort={sort}
              onSortChange={setSort}
              stickyHeader
              caption={`Trade history, ${isPaper ? 'paper' : 'live'} ledger`}
            />
          )}
        </div>
      </Panel>
    </div>
  );
}
