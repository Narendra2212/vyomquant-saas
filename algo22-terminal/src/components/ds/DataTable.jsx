/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/DataTable — the one table
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.9. design.md §11.3, §13.2c, §11.7.
 * Requirements 11.1, 11.3, 11.4, 11.6, 15.4, 17.2, 18.1.
 *
 * Replaces `Table` / `TableHead` / `TableHeader` / `TableRow` / `TableCell` in
 * `ui-legacy/primitives.jsx` **and** the raw `<table>` markup in `TradeHistory`,
 * `Strategies`, `Portfolio` and `PaperTrading`. Those five call sites each decided
 * alignment, padding, sort behaviour and empty copy for themselves, which is why
 * Requirement 11.3 ("align all numeric columns consistently") was false: every
 * numeric cell in `TradeHistory` is left-aligned today, and nothing in the code
 * said it should not be.
 *
 * ALIGNMENT IS A COLUMN PROPERTY, NOT A PER-CELL DECISION
 * ------------------------------------------------------
 * This is the whole reason the component is declarative. `align: 'numeric'` on the
 * column yields `text-align: right`, `font-variant-numeric: tabular-nums` and
 * `--font-mono` for the header *and* every cell in that column, and there is no
 * per-cell alignment prop to disagree with it. Requirement 11.3 then holds by
 * construction rather than by review: a cell cannot be misaligned without the
 * column being misdeclared, and every cell in the column is wrong together — which
 * is visible — instead of one being wrong quietly. Property 19 asserts exactly
 * that, over generated column sets, which is why each cell also carries
 * `data-align` and `data-column-key`.
 *
 * SORTING IS STABLE BECAUSE THE COMPARATOR SAYS SO
 * -----------------------------------------------
 * `Array.prototype.sort` has been required to be stable since ES2019, but relying
 * on that makes stability a property of the engine rather than of this module. So
 * rows are decorated with their input index and the comparator falls through to it
 * on every tie: `sign * compare(a, b) || a.index - b.index`. Equal keys therefore
 * keep their input order in both directions, and `direction` only ever *negates* a
 * total comparator — it never switches to a different one and never re-buckets
 * missing values. Both halves matter for Property 29: negation keeps "ordered by
 * the column's comparator" true for `desc` as well as `asc`, and sorting always
 * starts from the incoming `rows` array rather than from the previous render's
 * output, so toggling direction twice returns the identical order.
 *
 * A missing or unreadable value compares as the LOWEST value, deliberately. It
 * groups the unknowns at one end (last under `desc`, first under `asc`) without
 * needing a "nulls last" rule, which would have made `desc` something other than
 * the negation of `asc` and would have cost the property above.
 *
 * PAGINATION, NOT VIRTUALIZATION
 * ------------------------------
 * Requirement 11.4 says "pagination **or** virtualization". design.md §11.3 picks
 * pagination: the sticky header stays trivially correct, Ctrl-F finds what is on
 * screen, and no dependency is added — `ag-grid-community` was removed from
 * `package.json` in M1 precisely so that this decision could not be quietly
 * reversed. {@link pageSlice} is the whole mechanism, and it is exported because
 * Property 20 ("pagination partitions the row set exactly once") is a statement
 * about that function: pages `1…pageCount` cover every row exactly once, and any
 * page outside that range is empty rather than clamped. Clamping would have made a
 * stale page number silently repeat the last page's rows, which is precisely the
 * duplication the property forbids.
 *
 * SERVER-SIDE AND CLIENT-SIDE PAGINATION, WITHOUT A MODE FLAG
 * ---------------------------------------------------------
 * `totalCount` is the size of the row set this table pages over, after filtering.
 * When it exceeds `rows.length` the rows on hand cannot be the whole set, so they
 * are already one server page (`limit`/`offset`) and are rendered as given. When it
 * is absent or equal to `rows.length` the full set is here and the slice happens
 * locally. That is one derived boolean instead of a `paginationMode` prop nobody
 * would keep in step with the endpoint. A page doing its own client-side filtering
 * must pass the *filtered* count here; `FilterBar` owns the unfiltered/filtered
 * pair that `EmptyState` needs to tell Requirement 11.5's two cases apart.
 *
 * EMPTY RENDERS NO ROWS REGION AT ALL
 * -----------------------------------
 * No `<tbody>`, no `<td colSpan={8}>No trades found</td>`, no empty state of its
 * own. `Panel` renders `EmptyState`, which is the only component that knows how to
 * distinguish "no trades" from "no trades match these filters" (Requirement 11.5)
 * and is the only one that requires the next action (Requirement 14.1). A table
 * that rendered its own one-line message is what made those two cases
 * indistinguishable on four pages.
 *
 * BELOW THE LAPTOP BREAKPOINT (Requirement 17.2)
 * ---------------------------------------------
 * `priority: 3` columns are hidden and surfaced in a per-row expand, and the table
 * sits in an `overflow-x: auto` wrapper with `scrollbar-gutter: stable` so the
 * remaining columns scroll instead of being clipped. The reduction is CSS
 * (`max-laptop:` / `laptop:` variants off `--breakpoint-laptop`), not a
 * `matchMedia` listener: the DOM is then identical at every width, there is no
 * resize re-render, and nothing depends on a media-query implementation that jsdom
 * does not really have. The expander and the detail row are `laptop:hidden`, so a
 * row expanded at tablet width cannot leave a stray detail row behind when the
 * window grows.
 *
 * ONE TICK RE-RENDERS ONE `<tr>` (design.md §13.2c)
 * ------------------------------------------------
 * Rows are `memo`ised on `getRowId(row)` plus a shallow compare of the projected
 * cell values. The `row` object itself is deliberately **excluded** from that
 * compare: a WebSocket tick that rebuilds the page's row array hands every row a
 * new object identity while changing the values of one, and comparing identities
 * would re-render all fifty. The consequence to know about is the other side of
 * the same coin — a `render` component that reads a field of `row` which no column
 * projects can hold a stale value, because nothing observed it changing. Declare
 * the field as a column (it may be a `priority: 3` one) rather than reaching past
 * the projection.
 *
 * Row activation handlers are created once and look the current row up through a
 * ref, so they stay referentially stable across ticks. A `() => onRowClick(row)`
 * closure per row per render would change identity on every tick and defeat the
 * memo it is passed to.
 */

import React, { memo, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  ChevronUp,
} from 'lucide-react';
import { Link, useInRouterContext } from 'react-router-dom';

import { assertContract, hasText, warnContract } from './devAssert';
import { SKELETON_GEOMETRY } from './LoadingState';

/* ══════════════════════════════════════════════════════════════════════════
 * THE COLUMN CONTRACT
 * ══════════════════════════════════════════════════════════════════════════ */

/** `numeric` is the one that carries behaviour; everything else reads as text. */
export const COLUMN_ALIGNMENTS = Object.freeze(['text', 'numeric']);

/**
 * What `format` may say. It chooses the comparator and the default rendering, and
 * nothing else — colour never comes from here (that is `design/semantic.js`, via a
 * `render` component such as `PnLDisplay` or `StatusBadge`).
 *
 * `symbol` renders as text and sorts as text; it exists so a market column declares
 * what it holds instead of defaulting into the same branch by accident.
 */
export const COLUMN_FORMATS = Object.freeze(['text', 'symbol', 'number', 'currency', 'timestamp']);

/** Multi-column sorting is not supported by design (design.md §11.3). */
export const SORT_DIRECTIONS = Object.freeze(['asc', 'desc']);

/**
 * The priority at which a column stops being shown below `--breakpoint-laptop`.
 *
 * `>=` rather than `===`: a column declared `priority: 4` is at least as droppable
 * as a `3`, and silently keeping it would be the opposite of what its author asked
 * for.
 */
export const DROPPED_PRIORITY = 3;

/** design.md §7.7 — 50 rows per page on Trade History, and a sane default elsewhere. */
export const DEFAULT_PAGE_SIZE = 50;

/** The not-available marker (design.md §3.2, §5.2). Never `0`, never blank. */
export const NOT_AVAILABLE = '—';

/**
 * Row and header heights, and the padding that fills them.
 *
 * `compact` reads its two heights from `LoadingState`'s `SKELETON_GEOMETRY`, which
 * declares them for exactly this purpose: the skeleton and the real table are the
 * same height, so arrival shifts nothing (Requirement 14.2). The horizontal padding
 * is `px-1.5` (6px) because two adjacent cells then sit `columnGap: 12` apart, the
 * same 12px the skeleton lays out. The 1px row rule is the skeleton's `rowGap: 1`.
 *
 * `comfortable`'s 40px is declared here and only here. `SKELETON_GEOMETRY` models
 * the compact table because that is the density every in-scope page uses; the first
 * page to ship a comfortable table should move this number there rather than let a
 * second copy appear.
 */
const DENSITY = Object.freeze({
  compact: Object.freeze({
    rowHeight: SKELETON_GEOMETRY.table.rowHeight,
    headerHeight: SKELETON_GEOMETRY.table.headerHeight,
    padding: 'px-1.5 py-1',
    text: 'text-small',
  }),
  comfortable: Object.freeze({
    rowHeight: 40,
    headerHeight: SKELETON_GEOMETRY.table.headerHeight,
    padding: 'px-3 py-2',
    text: 'text-body',
  }),
});

/** `'compact' | 'comfortable'`, derived so the two lists cannot diverge. */
export const DENSITIES = Object.freeze(Object.keys(DENSITY));

/**
 * How tall a sticky-header table may grow before it scrolls internally.
 *
 * `stickyHeader` is inert without this. `overflow-x: auto` on the wrapper makes the
 * used value of `overflow-y` `auto` as well, so `position: sticky; top: 0` resolves
 * against the wrapper rather than the page — and a wrapper with no height limit
 * never scrolls, so the header would simply travel up the page with the rest of the
 * table. Bounding the wrapper is what makes the header actually stick. A caller that
 * needs a different bound passes `style={{ maxHeight }}`, which is merged last.
 */
const STICKY_MAX_HEIGHT = '70dvh';

const EMPTY_ROWS = Object.freeze([]);
const EMPTY_SET = Object.freeze(new Set());

/* ══════════════════════════════════════════════════════════════════════════
 * VALUE READING — total, and never inherited
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * `row[key]`, own properties only.
 *
 * `hasOwnProperty` rather than a bare index because a column keyed `constructor` or
 * `toString` would otherwise read a function off `Object.prototype` and render it.
 * The strict form makes a mistyped column key render not-available, which is the
 * honest answer to "this field does not exist".
 */
export function readCell(row, key) {
  if (row === null || typeof row !== 'object') return undefined;
  if (typeof key !== 'string') return undefined;
  return Object.prototype.hasOwnProperty.call(row, key) ? row[key] : undefined;
}

/** The cell values a row contributes, in column order. The memo compare's subject. */
export function projectRow(row, columns) {
  return columns.map((column) => readCell(row, column.key));
}

/**
 * A finite number, or `null`.
 *
 * Numeric strings are parsed because money and quantities arrive as decimal strings
 * from the backend. Booleans do not: `Number(true) === 1` would let a flag sort and
 * render as a quantity, which hides the mistake instead of showing it.
 */
export function toFiniteNumber(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (trimmed === '') return null;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/**
 * Epoch milliseconds, or `null`.
 *
 * A bare numeric *string* is not accepted as a timestamp: `'1700000000'` could be
 * seconds or milliseconds and guessing wrong moves a trade by fifty years. Numbers
 * are taken as milliseconds, which is what `Date.now()` and every backend timestamp
 * field in this app produce.
 */
export function toEpochMs(value) {
  if (value instanceof Date) {
    const time = value.getTime();
    return Number.isFinite(time) ? time : null;
  }
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'string') {
    const parsed = Date.parse(value.trim());
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/** A string that can be ordered, or `null`. Objects are unorderable, not `'[object Object]'`. */
function toComparableText(value) {
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : null;
  if (typeof value === 'boolean') return String(value);
  return null;
}

/* ══════════════════════════════════════════════════════════════════════════
 * COMPARATORS — one per format, each total over every input
 * ══════════════════════════════════════════════════════════════════════════ */

/** `null` sorts below everything, so unknowns group at one end without a special rule. */
function compareMissing(left, right) {
  if (left === null && right === null) return 0;
  if (left === null) return -1;
  return 1;
}

export function compareNumeric(a, b) {
  const left = toFiniteNumber(a);
  const right = toFiniteNumber(b);
  if (left === null || right === null) return compareMissing(left, right);
  if (left < right) return -1;
  return left > right ? 1 : 0;
}

export function compareTimestamp(a, b) {
  const left = toEpochMs(a);
  const right = toEpochMs(b);
  if (left === null || right === null) return compareMissing(left, right);
  if (left < right) return -1;
  return left > right ? 1 : 0;
}

/**
 * `localeCompare`, with no options.
 *
 * Deliberately plain: adding `{ numeric: true }` would order `BTC10` after `BTC9`,
 * which is nicer and is also a rule no caller declared. design.md §11.3 says
 * "string with `localeCompare`", so that is what a column gets.
 */
export function compareText(a, b) {
  const left = toComparableText(a);
  const right = toComparableText(b);
  if (left === null || right === null) return compareMissing(left, right);
  return left.localeCompare(right);
}

/**
 * The comparator a `format` selects.
 *
 * Total over unknown formats: anything unrecognised sorts as text rather than
 * throwing, because a format string is data and data can be wrong.
 */
export function comparatorFor(format) {
  switch (format) {
    case 'number':
    case 'currency':
      return compareNumeric;
    case 'timestamp':
      return compareTimestamp;
    default:
      return compareText;
  }
}

/**
 * Order `rows` by `sort`, stably.
 *
 * Returns the input array unchanged when there is nothing to do, so a memo upstream
 * of this sees no change. Sorting is applied only for a column that exists and
 * declares `sortable`: honouring a sort on a column whose header shows no `aria-sort`
 * would put the rendered order and the announced order into disagreement.
 *
 * @param {Array} rows
 * @param {Array} columns Normalised columns (see {@link normalizeColumns}).
 * @param {{key: string, direction: 'asc'|'desc'}} [sort]
 * @returns {Array} A new array, or `rows` itself when unsorted.
 */
export function sortRows(rows, columns, sort) {
  const list = Array.isArray(rows) ? rows : [];
  const key = sort && typeof sort.key === 'string' ? sort.key : null;
  if (!key) return list;

  const column = (Array.isArray(columns) ? columns : []).find((entry) => entry.key === key);
  if (!column || column.sortable !== true) return list;

  const compare = comparatorFor(column.format);
  const sign = sort.direction === 'desc' ? -1 : 1;

  return list
    .map((row, index) => ({ row, index }))
    .sort(
      (a, b) =>
        // The index fall-through is what makes this stable regardless of the
        // engine's own guarantees. `-0` is falsy, so a reversed tie still falls
        // through rather than reporting `-0` as an ordering.
        sign * compare(readCell(a.row, key), readCell(b.row, key)) || a.index - b.index,
    )
    .map((entry) => entry.row);
}

/* ══════════════════════════════════════════════════════════════════════════
 * PAGINATION — a partition, expressed as two pure functions
 * ══════════════════════════════════════════════════════════════════════════ */

/** How many pages `total` rows make. At least one, so "Page 1 of 0" cannot happen. */
export function pageCount(total, pageSize) {
  const rows = Number.isFinite(total) && total > 0 ? Math.trunc(total) : 0;
  const size = Number.isFinite(pageSize) && pageSize > 0 ? Math.trunc(pageSize) : 0;
  if (size === 0) return 1;
  return Math.max(1, Math.ceil(rows / size));
}

/**
 * The rows on `page`. **1-based**: page 1 is rows `0…pageSize-1`.
 *
 * Out-of-range pages return nothing rather than being clamped to the nearest real
 * page. Clamping is the tempting behaviour and it is what would break Property 20:
 * a stale `page` would silently re-render the last page's rows, so walking the pages
 * would yield some ids twice. An empty page is visible; a duplicated one is not.
 *
 * A non-positive or non-finite `pageSize` means "do not paginate" and returns every
 * row, which is how a small embedded table (Dashboard's five positions) opts out.
 */
export function pageSlice(rows, page, pageSize) {
  const list = Array.isArray(rows) ? rows : [];
  const size = Number.isFinite(pageSize) && pageSize > 0 ? Math.trunc(pageSize) : 0;
  if (size === 0) return list;
  const index = Number.isFinite(page) ? Math.trunc(page) : 1;
  if (index < 1) return [];
  const start = (index - 1) * size;
  if (start >= list.length) return [];
  return list.slice(start, start + size);
}

/* ══════════════════════════════════════════════════════════════════════════
 * DISPLAY FORMATTING — grouped, and never rounded
 * ══════════════════════════════════════════════════════════════════════════ */

/** `'12'`, `'-3.50'`, `'+0.7'` — a decimal we can group without reinterpreting. */
const PLAIN_DECIMAL = /^[+-]?\d+(?:\.\d+)?$/;

/**
 * Thousands separators, added by hand.
 *
 * Not `Intl.NumberFormat`: its separators are locale-dependent, and a terminal that
 * renders `1.234,5` in one panel and `1,234.5` in another has invented a hazard out
 * of a formatting default. `maximumFractionDigits` would also round, and §11.2's
 * never-round discipline applies to every money figure, not just the inputs.
 *
 * The input is left exactly as it arrived to the right of the point, so a server
 * string of `"1.50"` keeps its trailing zero. Exponential notation is returned
 * untouched, because grouping it would be a lie about its magnitude.
 */
function groupDecimal(text) {
  if (/[eE]/.test(text)) return text;
  const negative = text.startsWith('-');
  const unsigned = negative || text.startsWith('+') ? text.slice(1) : text;
  const [whole, fraction] = unsigned.split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const sign = negative ? '-' : '';
  return fraction === undefined ? `${sign}${grouped}` : `${sign}${grouped}.${fraction}`;
}

/** `2024-03-11 12:04:00Z`. UTC, to the second, in the order a trader scans. */
function formatTimestamp(epochMs) {
  return `${new Date(epochMs).toISOString().slice(0, 19).replace('T', ' ')}Z`;
}

/**
 * What a cell shows when the column declares no `render`.
 *
 * Returns `null` for anything that has no honest rendering — `null`, `undefined`,
 * the empty string, `NaN`, an unparseable date, an object. The caller turns that
 * into the not-available marker, never into `0` and never into a blank cell
 * (Requirement 14.5).
 *
 * @param {unknown} value
 * @param {string} format One of {@link COLUMN_FORMATS}.
 * @returns {string|null}
 */
export function formatCellValue(value, format) {
  if (value === null || value === undefined) return null;

  if (format === 'timestamp') {
    const epochMs = toEpochMs(value);
    return epochMs === null ? null : formatTimestamp(epochMs);
  }

  if (format === 'number' || format === 'currency') {
    // A decimal string is grouped as a string so its trailing zeros survive; only a
    // value that is not already one goes through Number.
    if (typeof value === 'string' && PLAIN_DECIMAL.test(value.trim())) {
      return groupDecimal(value.trim());
    }
    const numeric = toFiniteNumber(value);
    return numeric === null ? null : groupDecimal(String(numeric));
  }

  // text / symbol / anything unrecognised.
  if (typeof value === 'string') return value.trim() === '' ? null : value;
  // `String(value)`, not `groupDecimal`: a text column holding 2024 is a version or
  // an id, and `2,024` would be wrong.
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : null;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return null;
}

/* ══════════════════════════════════════════════════════════════════════════
 * COLUMN NORMALISATION
 * ══════════════════════════════════════════════════════════════════════════ */

/** Tailwind classes for a cell in this column. The only place alignment is decided. */
function alignmentClasses(align) {
  return align === 'numeric'
    ? 'text-right font-mono tabular-nums'
    : 'text-left';
}

/**
 * Fill in every optional field once, up front.
 *
 * `format` follows `align` when it is not declared: a numeric column sorts
 * numerically. Without that rule the `pnl` column in design.md §11.3's worked
 * example — `align: 'numeric'`, `sortable: true`, `render: PnLDisplay`, no `format`
 * — would sort as text, and `-1200` would land between `-12` and `-13`.
 *
 * `index` is the column's position in the declared list, which is also its position
 * in the projected value array, so a cell can find its value without a lookup.
 */
function normalizeColumns(columns, density) {
  const list = Array.isArray(columns) ? columns : [];
  const spacing = DENSITY[density];

  return Object.freeze(
    list.map((column, index) => {
      const source = column && typeof column === 'object' ? column : {};
      const align = source.align === 'numeric' ? 'numeric' : 'text';
      const declaredFormat = COLUMN_FORMATS.includes(source.format) ? source.format : null;
      const format = declaredFormat ?? (align === 'numeric' ? 'number' : 'text');
      const priority = Number.isFinite(source.priority) ? Math.trunc(source.priority) : 1;

      return Object.freeze({
        index,
        key: typeof source.key === 'string' ? source.key : '',
        header: source.header,
        align,
        format,
        priority,
        sortable: source.sortable === true,
        render: typeof source.render === 'function' ? source.render : null,
        width: source.width,
        /** Hidden below the laptop breakpoint, and surfaced in the per-row expand. */
        dropped: priority >= DROPPED_PRIORITY,
        cellClassName: `${spacing.padding} ${spacing.text} ${alignmentClasses(align)}`,
        headClassName: `${spacing.padding} text-micro font-semibold uppercase tracking-wider `
          + `text-content-secondary ${alignmentClasses(align)}`,
      });
    }),
  );
}

/** `width: 132` is pixels; `width: '20%'` is itself. Undefined leaves the browser in charge. */
function widthStyle(width) {
  if (width === undefined || width === null) return undefined;
  return { width: typeof width === 'number' ? `${width}px` : width };
}

/** An id fragment safe to put in `aria-controls`, derived from the row id. */
function detailIdFor(base, rowId) {
  return `${base}detail-${String(rowId).replace(/[^\w-]/g, '_')}`;
}

/* ══════════════════════════════════════════════════════════════════════════
 * CELL AND ROW RENDERING
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The not-available marker.
 *
 * `content-muted` is 3.2:1 and is documented non-text-only (design.md §3.2); an
 * em-dash beside a labelled column header is the case that documentation names. The
 * glyph is hidden from assistive technology and the words are given instead, because
 * a screen reader reading "em dash" tells a trader nothing about whether the exchange
 * reported a liquidation price.
 */
function NotAvailable() {
  return (
    <>
      <span aria-hidden="true" className="text-content-muted">{NOT_AVAILABLE}</span>
      <span className="sr-only">Not available</span>
    </>
  );
}

/**
 * A column's `render` is a **component**, not a callback.
 *
 * design.md §11.3 writes `render: PnLDisplay`, `render: StatusBadge`,
 * `render: SideBadge` — component references. So it is rendered as
 * `<Render value={…} row={…} column={…} />`, which makes `render: PnLDisplay` work
 * verbatim and makes an inline adapter read the same way:
 * `render: ({ value }) => <StatusBadge state={value} />`.
 */
function renderCellContent(column, value, row) {
  if (column.render) {
    const Render = column.render;
    return <Render value={value} row={row} column={column} />;
  }
  // A row that already carries an element for this column renders it as given.
  if (React.isValidElement(value)) return value;
  const text = formatCellValue(value, column.format);
  return text === null ? <NotAvailable /> : text;
}

/**
 * One `<td>`.
 *
 * `Wrapper` arrives for the first cell only, and only when `rowHref` is set: wrapping
 * the first cell's content in a real link is what puts the row in the tab order
 * (Requirement 18.1) without making a `<tr>` pretend to be a control.
 */
function DataCell({ column, value, row, wrapper: Wrapper, wrapperProps }) {
  const content = renderCellContent(column, value, row);

  return (
    <td
      data-column-key={column.key}
      data-align={column.align}
      data-priority={column.priority}
      className={`${column.cellClassName} text-content-primary${column.dropped ? ' max-laptop:hidden' : ''}`}
    >
      {Wrapper ? <Wrapper {...wrapperProps}>{content}</Wrapper> : content}
    </td>
  );
}

/** Shallow, `Object.is`-based. The projected values are primitives in every real table. */
function sameValues(left, right) {
  if (left === right) return true;
  if (!left || !right || left.length !== right.length) return false;
  for (let index = 0; index < left.length; index += 1) {
    if (!Object.is(left[index], right[index])) return false;
  }
  return true;
}

/**
 * The memo compare. `row` is absent from it on purpose — see the module docblock.
 *
 * Everything else here is either a primitive or an identity the parent holds stable
 * across renders (`columns` and `droppedColumns` come from a `useMemo`; `onActivate`
 * and `onToggleExpand` from a `useCallback` with no dependencies).
 */
function rowPropsEqual(previous, next) {
  return (
    previous.rowId === next.rowId
    && previous.columns === next.columns
    && previous.droppedColumns === next.droppedColumns
    && previous.href === next.href
    && previous.rowHeight === next.rowHeight
    && previous.paddingClass === next.paddingClass
    && previous.textClass === next.textClass
    && previous.expanded === next.expanded
    && previous.detailColSpan === next.detailColSpan
    && previous.detailId === next.detailId
    && previous.onActivate === next.onActivate
    && previous.onToggleExpand === next.onToggleExpand
    && previous.linkable === next.linkable
    && sameValues(previous.values, next.values)
  );
}

const DataTableRow = memo(function DataTableRow({
  rowId,
  row,
  values,
  columns,
  droppedColumns,
  href,
  rowHeight,
  paddingClass,
  textClass,
  expanded,
  detailColSpan,
  detailId,
  onActivate,
  onToggleExpand,
  linkable,
}) {
  // A row is keyboard-activated through its first cell's link when it has an href;
  // only the handler-without-href case makes the row itself focusable.
  const rowActivates = typeof onActivate === 'function' && !href;

  const handleClick = useCallback(
    (event) => {
      if (rowActivates) onActivate(rowId, event);
    },
    [onActivate, rowActivates, rowId],
  );

  const handleKeyDown = useCallback(
    (event) => {
      if (!rowActivates) return;
      if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
        // Space scrolls the page by default, which would move the table out from
        // under the row the trader just activated.
        event.preventDefault();
        onActivate(rowId, event);
      }
    },
    [onActivate, rowActivates, rowId],
  );

  const handleToggle = useCallback(() => onToggleExpand(rowId), [onToggleExpand, rowId]);

  const interactive = rowActivates || Boolean(href);

  // `Link` inside a router, a plain anchor outside one — `ds/ActionControl`'s
  // precedent. A primitive that hard-fails without a router cannot be rendered in
  // isolation, and the property tests for this component render it bare.
  const linkWrapper = href ? (linkable ? Link : 'a') : null;
  const linkProps = href
    ? {
      ...(linkable ? { to: href } : { href }),
      className: 'text-content-primary hover:text-brand',
      // With an href the anchor is the activation surface, so `onRowClick` becomes
      // the interception hook: call `preventDefault()` in it to route yourself.
      ...(typeof onActivate === 'function'
        ? { onClick: (event) => onActivate(rowId, event) }
        : null),
    }
    : null;

  const expandable = droppedColumns.length > 0;
  const rowLabel = describeRow(columns, values, rowId);

  return (
    <>
      <tr
        data-row-id={String(rowId)}
        style={{ height: rowHeight }}
        className={`border-b border-line-subtle${
          interactive ? ' cursor-pointer hover:bg-surface-raised' : ''
        }`}
        tabIndex={rowActivates ? 0 : undefined}
        onClick={rowActivates ? handleClick : undefined}
        onKeyDown={rowActivates ? handleKeyDown : undefined}
      >
        {expandable ? (
          <td data-row-expander="true" className={`${paddingClass} laptop:hidden`}>
            <button
              type="button"
              onClick={handleToggle}
              aria-expanded={expanded}
              aria-controls={detailId}
              className="inline-flex items-center rounded-sm text-content-secondary hover:text-content-primary"
            >
              {expanded ? (
                <ChevronDown size={14} aria-hidden="true" />
              ) : (
                <ChevronRight size={14} aria-hidden="true" />
              )}
              <span className="sr-only">
                {`${expanded ? 'Hide' : 'Show'} remaining columns for ${rowLabel}`}
              </span>
            </button>
          </td>
        ) : null}

        {columns.map((column) => (
          <DataCell
            key={column.key}
            column={column}
            value={values[column.index]}
            row={row}
            wrapper={column.index === 0 ? linkWrapper : null}
            wrapperProps={linkProps}
          />
        ))}
      </tr>

      {expandable && expanded ? (
        <tr id={detailId} data-row-detail="true" className="laptop:hidden bg-surface-raised">
          <td colSpan={detailColSpan} className={`${paddingClass} ${textClass}`}>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1">
              {droppedColumns.map((column) => (
                <React.Fragment key={column.key}>
                  <dt className="text-micro uppercase tracking-wider text-content-secondary">
                    {column.header}
                  </dt>
                  <dd
                    data-column-key={column.key}
                    data-align={column.align}
                    className={`${alignmentClasses(column.align)} text-content-primary`}
                  >
                    {renderCellContent(column, values[column.index], row)}
                  </dd>
                </React.Fragment>
              ))}
            </dl>
          </td>
        </tr>
      ) : null}
    </>
  );
}, rowPropsEqual);

/** The first shown column's text, for the expander's accessible name. */
function describeRow(columns, values, rowId) {
  const first = columns.find((column) => !column.dropped);
  const text = first ? formatCellValue(values[first.index], first.format) : null;
  return text ?? `row ${rowId}`;
}

/* ══════════════════════════════════════════════════════════════════════════
 * HEADER
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * Two states, not three.
 *
 * An `asc → desc → unsorted` cycle is a common third state and it would cost
 * Property 29: "toggling direction twice restores the original" is only true of a
 * two-state toggle. Clicking a new column starts at `asc`.
 */
export function nextSort(current, key) {
  const active = Boolean(current) && current.key === key;
  return { key, direction: active && current.direction === 'asc' ? 'desc' : 'asc' };
}

/** `aria-sort`'s vocabulary, which is not `asc`/`desc`. */
function ariaSortFor(sortable, direction) {
  if (!sortable) return undefined;
  if (direction === 'asc') return 'ascending';
  if (direction === 'desc') return 'descending';
  return 'none';
}

function SortGlyph({ direction }) {
  if (direction === 'asc') return <ChevronUp size={12} aria-hidden="true" />;
  if (direction === 'desc') return <ChevronDown size={12} aria-hidden="true" />;
  return <ChevronsUpDown size={12} aria-hidden="true" className="text-content-muted" />;
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE COMPONENT
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The one table.
 *
 * @param {Object} props
 * @param {Array<{key: string, header: React.ReactNode, align?: 'text'|'numeric',
 *   sortable?: boolean, format?: 'text'|'symbol'|'number'|'currency'|'timestamp',
 *   render?: Function, width?: number|string, priority?: number}>} props.columns
 *   Declarative. `align` decides alignment for the whole column, `format` picks the
 *   comparator and the default rendering, `render` is a component, `priority: 3`
 *   drops the column below `--breakpoint-laptop`.
 * @param {Array<Object>} props.rows One page's worth when `totalCount` says so, the
 *   whole (filtered) set otherwise.
 * @param {(row: Object, index: number) => string|number} [props.getRowId] Defaults to
 *   `row.id`, then the index. It is the memo key, so a row that changes id is a new row.
 * @param {{key: string, direction: 'asc'|'desc'}} [props.sort] Controlled. Single column.
 * @param {(sort: {key: string, direction: 'asc'|'desc'}) => void} [props.onSortChange]
 *   Absent means the headers are text, not dead buttons (Requirement 19.4).
 * @param {number} [props.page] **1-based.**
 * @param {number} [props.pageSize] `<= 0` disables pagination.
 * @param {(page: number) => void} [props.onPageChange] Receives the new 1-based page.
 * @param {number} [props.totalCount] The size of the set being paged over, after
 *   filtering. Greater than `rows.length` means the rows are already a server page.
 * @param {boolean} [props.stickyHeader]
 * @param {'compact'|'comfortable'} [props.density]
 * @param {(row: Object, event: Event) => void} [props.onRowClick]
 * @param {(row: Object) => string} [props.rowHref] Renders the first cell as a link.
 * @param {string} props.caption REQUIRED — the table's accessible name (Requirement 18.4).
 * @param {string} [props.className] Applied to the scroll wrapper.
 * @param {Object} [props.style] Merged last onto the scroll wrapper.
 */
export function DataTable({
  columns,
  rows,
  getRowId,
  sort,
  onSortChange,
  page = 1,
  pageSize = DEFAULT_PAGE_SIZE,
  onPageChange,
  totalCount,
  stickyHeader = false,
  density = 'compact',
  onRowClick,
  rowHref,
  caption,
  className = '',
  style,
  ...rest
}) {
  // Requirement 18.4: a table with no accessible name is a grid of numbers with no
  // subject. It is the one prop this component will not render without.
  assertContract(
    hasText(caption),
    'DataTable: `caption` is required — it is the table\'s accessible name '
      + '(Requirement 18.4). Pass what the rows are, e.g. caption="Trade history, live account".',
  );

  const knownDensity = DENSITIES.includes(density);
  assertContract(
    knownDensity,
    `DataTable: \`density\` must be one of ${DENSITIES.join(' | ')}, received ${JSON.stringify(density)}.`,
  );
  const resolvedDensity = knownDensity ? density : 'compact';
  const spacing = DENSITY[resolvedDensity];

  assertContract(
    Array.isArray(columns) && columns.length > 0,
    'DataTable: `columns` must be a non-empty array. A table declares its columns; '
      + 'it does not derive them from the first row, which is how a column disappears '
      + 'the moment one row omits a field.',
  );

  const normalizedColumns = useMemo(
    () => normalizeColumns(columns, resolvedDensity),
    [columns, resolvedDensity],
  );

  assertContract(
    normalizedColumns.every((column) => column.key !== ''),
    'DataTable: every column needs a string `key` — it is the row field to read, the '
      + 'sort key and the React key.',
  );
  assertContract(
    new Set(normalizedColumns.map((column) => column.key)).size === normalizedColumns.length,
    'DataTable: column `key`s must be unique. Two columns sharing a key sort as one '
      + 'and render the same value twice.',
  );
  assertContract(
    normalizedColumns.every((column) => column.header !== undefined && column.header !== null
      && column.header !== ''),
    'DataTable: every column needs a `header` — it is the accessible name of the '
      + 'column for every cell beneath it.',
  );

  const droppedColumns = useMemo(
    () => Object.freeze(normalizedColumns.filter((column) => column.dropped)),
    [normalizedColumns],
  );

  const safeRows = Array.isArray(rows) ? rows : EMPTY_ROWS;
  const total = Number.isFinite(totalCount) && totalCount >= 0
    ? Math.trunc(totalCount)
    : safeRows.length;

  // The rows on hand cannot be the whole set, so they are already a server page.
  const preSliced = safeRows.length < total;

  const sortedRows = useMemo(
    () => sortRows(safeRows, normalizedColumns, sort),
    [safeRows, normalizedColumns, sort],
  );

  const visibleRows = useMemo(
    () => (preSliced ? sortedRows : pageSlice(sortedRows, page, pageSize)),
    [preSliced, sortedRows, page, pageSize],
  );

  const resolveRowId = useMemo(
    () => (typeof getRowId === 'function'
      ? getRowId
      : (row, index) => (readCell(row, 'id') ?? index)),
    [getRowId],
  );

  const entries = useMemo(
    () => visibleRows.map((row, index) => ({
      id: resolveRowId(row, index),
      row,
      values: projectRow(row, normalizedColumns),
      href: typeof rowHref === 'function' ? rowHref(row) : undefined,
    })),
    [visibleRows, normalizedColumns, resolveRowId, rowHref],
  );

  /*
   * The freshest rows, reachable from a handler that never changes identity. Written
   * in an effect rather than during render: effects commit before any user event can
   * fire, so the lookup is current, and the row handlers below can then be
   * `useCallback(…, [])` and stay stable across every tick.
   */
  const latest = useRef({ entries, onRowClick });
  useEffect(() => {
    latest.current = { entries, onRowClick };
  });

  const handleActivate = useCallback((rowId, event) => {
    const { entries: current, onRowClick: handler } = latest.current;
    if (typeof handler !== 'function') return;
    const entry = current.find((candidate) => candidate.id === rowId);
    if (entry) handler(entry.row, event);
  }, []);

  const [expandedIds, setExpandedIds] = useState(EMPTY_SET);
  const handleToggleExpand = useCallback((rowId) => {
    setExpandedIds((previous) => {
      const next = new Set(previous);
      if (next.has(rowId)) next.delete(rowId);
      else next.add(rowId);
      return next;
    });
  }, []);

  const sortingEnabled = typeof onSortChange === 'function';
  const handleSort = useCallback(
    (key) => {
      if (typeof onSortChange === 'function') onSortChange(nextSort(sort, key));
    },
    [onSortChange, sort],
  );

  const idBase = useId();
  const inRouter = useInRouterContext();
  const rowsAreLinks = typeof rowHref === 'function';

  const pages = pageCount(total, pageSize);
  const currentPage = Math.min(Math.max(Number.isFinite(page) ? Math.trunc(page) : 1, 1), pages);
  const paginated = pages > 1;
  warnContract(
    !paginated || typeof onPageChange === 'function',
    `DataTable (${caption || 'untitled'}): ${total} rows at ${pageSize} per page needs `
      + '`onPageChange`, or the trader can see the first page only. Rendering the count '
      + 'without the controls, because a control that cannot move is worse than none '
      + '(Requirement 19.4).',
  );
  const firstShown = paginated ? (currentPage - 1) * pageSize + 1 : 1;
  const lastShown = Math.min(currentPage * pageSize, total);

  /*
   * The expander occupies a column of its own below the laptop breakpoint, and the
   * detail row spans everything still shown there — which is the columns that were
   * not dropped, plus the expander.
   */
  const detailColSpan = normalizedColumns.length - droppedColumns.length + 1;

  return (
    <div className={`w-full${className ? ` ${className}` : ''}`} {...rest}>
      <div
        className="w-full overflow-x-auto"
        style={{
          scrollbarGutter: 'stable',
          ...(stickyHeader ? { maxHeight: STICKY_MAX_HEIGHT, overflowY: 'auto' } : null),
          ...style,
        }}
      >
        <table
          data-density={resolvedDensity}
          className="w-full border-collapse"
          style={{ minWidth: 'max-content' }}
        >
          {/* The accessible name, not a visible title — the enclosing `Panel` carries
              the heading a sighted trader reads. */}
          <caption className="sr-only">{caption}</caption>

          <thead>
            <tr className="border-b border-line-default">
              {droppedColumns.length > 0 ? (
                <th scope="col" className={`${spacing.padding} laptop:hidden`} style={{ width: '1%' }}>
                  <span className="sr-only">Expand row</span>
                </th>
              ) : null}

              {normalizedColumns.map((column) => {
                const active = sort && sort.key === column.key;
                const direction = active
                  ? (sort.direction === 'desc' ? 'desc' : 'asc')
                  : null;
                const interactive = column.sortable && sortingEnabled;

                return (
                  <th
                    key={column.key}
                    scope="col"
                    data-column-key={column.key}
                    data-align={column.align}
                    data-priority={column.priority}
                    data-sortable={column.sortable ? 'true' : 'false'}
                    aria-sort={ariaSortFor(column.sortable, direction)}
                    className={`${column.headClassName}${column.dropped ? ' max-laptop:hidden' : ''}${
                      stickyHeader ? ' sticky top-0 bg-surface-panel' : ''
                    }`}
                    style={{
                      height: spacing.headerHeight,
                      ...widthStyle(column.width),
                      ...(stickyHeader ? { zIndex: 'var(--z-sticky)' } : null),
                    }}
                  >
                    {interactive ? (
                      <button
                        type="button"
                        onClick={() => handleSort(column.key)}
                        className={`inline-flex items-center gap-1 hover:text-content-primary${
                          // The glyph sits on the inside edge, so it never separates a
                          // right-aligned header from the digits beneath it.
                          column.align === 'numeric' ? ' flex-row-reverse' : ''
                        }`}
                      >
                        {column.header}
                        <SortGlyph direction={direction} />
                      </button>
                    ) : (
                      column.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>

          {/* Requirement 11.5 / design.md §11.3: no rows means NO rows region. The
              enclosing `Panel` renders `EmptyState`, which is the only component that
              can tell "no trades" from "no trades match these filters". */}
          {entries.length > 0 ? (
            <tbody>
              {entries.map((entry) => (
                <DataTableRow
                  key={entry.id}
                  rowId={entry.id}
                  row={entry.row}
                  values={entry.values}
                  columns={normalizedColumns}
                  droppedColumns={droppedColumns}
                  href={entry.href}
                  rowHeight={spacing.rowHeight}
                  paddingClass={spacing.padding}
                  textClass={spacing.text}
                  expanded={expandedIds.has(entry.id)}
                  detailColSpan={detailColSpan}
                  detailId={detailIdFor(idBase, entry.id)}
                  onActivate={typeof onRowClick === 'function' ? handleActivate : null}
                  onToggleExpand={handleToggleExpand}
                  linkable={rowsAreLinks && inRouter}
                />
              ))}
            </tbody>
          ) : null}
        </table>
      </div>

      {paginated ? (
        <div className="flex items-center justify-between gap-3 border-t border-line-subtle px-1.5 py-2">
          {/* design.md §11.3: the row count is a live region, so a page change is
              announced without moving focus. */}
          <p role="status" className="text-micro text-content-secondary">
            {`Showing ${firstShown}–${lastShown} of ${total}`}
          </p>

          {typeof onPageChange === 'function' ? (
            <nav aria-label="Pagination" className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => onPageChange(currentPage - 1)}
                disabled={currentPage <= 1}
                className="inline-flex items-center rounded-sm border border-line-strong p-1 text-content-secondary hover:text-content-primary disabled:opacity-40"
              >
                <ChevronLeft size={14} aria-hidden="true" />
                <span className="sr-only">Previous page</span>
              </button>

              <span className="text-micro text-content-secondary">
                {`Page ${currentPage} of ${pages}`}
              </span>

              <button
                type="button"
                onClick={() => onPageChange(currentPage + 1)}
                disabled={currentPage >= pages}
                className="inline-flex items-center rounded-sm border border-line-strong p-1 text-content-secondary hover:text-content-primary disabled:opacity-40"
              >
                <ChevronRight size={14} aria-hidden="true" />
                <span className="sr-only">Next page</span>
              </button>
            </nav>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default DataTable;
