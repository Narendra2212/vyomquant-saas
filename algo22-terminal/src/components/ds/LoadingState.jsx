/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/LoadingState — the one loading affordance, in seven shapes
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.1. design.md §5.1, §11.1. Requirements 14.2, 3.5.
 *
 * Consolidates five legacy primitives into one: `Spinner`, `LoadingOverlay`,
 * `SkeletonLine`, `SkeletonCard` and `SkeletonTable` from
 * `components/ui-legacy/primitives.jsx`. Those five are left in place — task 6.26
 * and task 10.9 own their removal and the call-site migration; this is the
 * replacement they migrate onto.
 *
 * THE POINT OF THE `kind` PROP (Requirement 14.2)
 * ---------------------------------------------
 * Requirement 14.2 asks for a loading indicator "for that element" rather than a
 * page-wide one. `LoadingProvider`'s overlay is the counter-example this replaces:
 * it covers the entire application whenever any keyed loading flag is true, so a
 * background refresh of one panel blanks the other nine. Design.md §5.1 records
 * that the overlay's render is deleted (task 10.9); every panel gets its own
 * `LoadingState` instead, and `ds/Panel` requires `loading.kind` before it will
 * render one, so "which shape" is a decision that cannot be skipped.
 *
 * WHY THE GEOMETRY IS A TABLE AND NOT PER-COMPONENT NUMBERS
 * -------------------------------------------------------
 * "Arrival shifts nothing" is only true if the skeleton and the content it
 * replaces are the same height. Two sets of numbers in two files cannot stay equal,
 * so there is one set: {@link SKELETON_GEOMETRY}. This module reads it, and the
 * components that render the *real* content must read it too —
 * `ds/DataTable`'s compact row height, `ds/Chart`'s default height and
 * `ds/Metric`'s tier-1 figure box are all defined here, and those tasks
 * (6.11, 6.4, 6.16) import this constant rather than restating the value. A
 * change to a row height is then one edit that moves the table and its skeleton
 * together, which is the only arrangement in which they cannot drift.
 *
 * ACCESSIBILITY
 * -------------
 * One `role="status"` region per load, carrying the accessible name, with every
 * shimmer bar inside it `aria-hidden`. `aria-busy="true"` marks the region as
 * still resolving. The alternative — a live region per bar — announces "blank"
 * forty times for one table.
 */

import { token } from '../../design/tokens';

import { assertContract, hasText } from './devAssert';
import { Skeleton } from './Skeleton';

/**
 * The seven shapes real content takes, per design.md §5.1.
 *
 * Frozen and exported so `ds/Panel` validates `loading.kind` against this list
 * rather than re-spelling the strings, and so the property test for the panel
 * state contract (task 6.2) can enumerate them.
 */
export const LOADING_KINDS = Object.freeze([
  'skeleton-table',
  'skeleton-cards',
  'skeleton-chart',
  'skeleton-metric',
  'inline',
  'button',
  'page',
]);

/**
 * THE SINGLE SOURCE FOR "HOW BIG IS THE CONTENT THIS REPLACES".
 *
 * Pixels, not tokens, because these are content dimensions rather than palette
 * values: a table row is 32px tall because that is the compact density design.md
 * §11.3 specifies, and `tokens.css` has no `--row-height` to spend on it. Every
 * number below is consumed in exactly two places — the skeleton in this file and
 * the real component named in its comment — and nowhere else.
 *
 * @type {Readonly<Object>}
 */
export const SKELETON_GEOMETRY = Object.freeze({
  /** `ds/DataTable`, `density="compact"` (design.md §11.3). */
  table: Object.freeze({
    headerHeight: 28,
    rowHeight: 32,
    rowGap: 1,
    columnGap: 12,
    cellHeight: 10,
  }),
  /** A summary/strategy card. */
  card: Object.freeze({
    height: 88,
    gap: 12,
  }),
  /** `ds/Chart`'s default plot area (design.md §15.5 / Requirement 15.5). */
  chart: Object.freeze({
    height: 240,
    axisHeight: 10,
  }),
  /** `ds/Metric`, tier 1: `--text-micro` label over a `--text-figure` figure. */
  metric: Object.freeze({
    labelHeight: 10,
    figureHeight: 28,
    gap: 8,
  }),
  /** A page's own loading shape: `ds/PageHeader`'s reserved block, then content. */
  page: Object.freeze({
    headerHeight: 64,
    gap: 16,
  }),
});

/** Defaults per kind, so `rows`/`columns` may be omitted without producing nothing. */
const DEFAULT_COUNTS = Object.freeze({
  'skeleton-table': Object.freeze({ rows: 5, columns: 4 }),
  'skeleton-cards': Object.freeze({ rows: 1, columns: 3 }),
  'skeleton-chart': Object.freeze({ rows: 1, columns: 1 }),
  'skeleton-metric': Object.freeze({ rows: 1, columns: 4 }),
  inline: Object.freeze({ rows: 1, columns: 1 }),
  button: Object.freeze({ rows: 1, columns: 1 }),
  page: Object.freeze({ rows: 5, columns: 4 }),
});

/** The label a kind falls back to when the caller supplies none. */
const DEFAULT_LABEL = 'Loading';

/** The widest skeleton this module will render. Above it, the shape stops being informative. */
const MAX_COUNT = 40;

/**
 * A caller-supplied count, made safe without silently becoming nothing.
 *
 * A skeleton whose row count came from `data.length` before the read finished can
 * arrive as `0`, `-1`, `2.5`, `NaN` or `'8'`. None of those is a reason to render
 * an empty region — the panel IS loading, and a loading panel that renders nothing
 * is indistinguishable from a broken one. So a count that is not a usable positive
 * integer falls back to the kind's default, and an absurdly large one is clamped.
 *
 * @param {unknown} value
 * @param {number} fallback
 * @returns {number} An integer in `[1, MAX_COUNT]`.
 */
function count(value, fallback) {
  const numeric = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  const whole = Math.trunc(numeric);
  if (whole < 1) return fallback;
  return Math.min(whole, MAX_COUNT);
}

/** A stable pseudo-random-looking width, so a skeleton row does not read as a solid block. */
const CELL_WIDTHS = Object.freeze(['92%', '64%', '78%', '55%', '86%', '70%']);

/**
 * The one rotating affordance. Replaces `Spinner` from `ui-legacy/primitives.jsx`,
 * which rendered its own `@keyframes spin` inside a `<style>` child on every
 * instance; the keyframes are now in `src/styles/ds.css`.
 *
 * Colour is `currentColor`, so it takes the token colour of whatever text it sits
 * beside and there is no colour prop to get out of step with the palette.
 *
 * Exported for `ds/CommandButton` (task 6.23), which hands it to `ui/Button` as the
 * icon while a command is in flight. It is NOT a primitive and the barrel must not
 * list it — the alternative was a second copy of this SVG in that file.
 */
export function Spinner({ size }) {
  return (
    <svg
      className="ds-spinner"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.5" strokeOpacity="0.25" />
      <path d="M14.5 8A6.5 6.5 0 0 0 8 1.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

/** One row of `columns` cell placeholders, on the table geometry. */
function TableRow({ columns, height }) {
  const { columnGap, cellHeight } = SKELETON_GEOMETRY.table;
  return (
    <div
      className="grid items-center"
      style={{
        height,
        columnGap,
        gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
      }}
    >
      {Array.from({ length: columns }, (_, index) => (
        <Skeleton key={index} height={cellHeight} width={CELL_WIDTHS[index % CELL_WIDTHS.length]} />
      ))}
    </div>
  );
}

/**
 * `skeleton-table` — a header rule plus `rows` body rows of `columns` cells.
 *
 * Total height is `headerHeight + rows × (rowHeight + rowGap)`, which is exactly
 * what `ds/DataTable` occupies for the same row and column counts.
 */
function TableSkeleton({ rows, columns }) {
  const { headerHeight, rowHeight, rowGap } = SKELETON_GEOMETRY.table;
  return (
    <div className="w-full" style={{ display: 'grid', rowGap }}>
      <div className="border-b border-line-default">
        <TableRow columns={columns} height={headerHeight} />
      </div>
      {Array.from({ length: rows }, (_, index) => (
        <TableRow key={index} columns={columns} height={rowHeight} />
      ))}
    </div>
  );
}

/** `skeleton-cards` — `rows × columns` card placeholders. Replaces `SkeletonCard`. */
function CardsSkeleton({ rows, columns }) {
  const { height, gap } = SKELETON_GEOMETRY.card;
  return (
    <div
      className="w-full"
      style={{ display: 'grid', gap, gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {Array.from({ length: rows * columns }, (_, index) => (
        <div
          key={index}
          className="flex flex-col justify-between rounded-md border border-line-subtle bg-surface-raised p-3"
          style={{ height }}
        >
          <Skeleton height={10} width="58%" />
          <Skeleton height={18} width="42%" />
          <Skeleton height={8} width="80%" />
        </div>
      ))}
    </div>
  );
}

/** `skeleton-chart` — the plot area and an axis rule, at `ds/Chart`'s own height. */
function ChartSkeleton() {
  const { height, axisHeight } = SKELETON_GEOMETRY.chart;
  return (
    <div className="flex w-full flex-col gap-2">
      {/* `radius` rather than a `rounded-md` class: `ds.css` is unlayered and
          Tailwind's utilities are in `@layer utilities`, so an unlayered
          `.ds-skeleton { border-radius }` outranks the utility whatever the
          source order. The prop is the only override that actually applies. */}
      <Skeleton height={height} radius={token.radius.md} />
      <Skeleton height={axisHeight} width="70%" />
    </div>
  );
}

/** `skeleton-metric` — `columns` figures across, `rows` deep, on `ds/Metric`'s tier-1 box. */
function MetricSkeleton({ rows, columns }) {
  const { labelHeight, figureHeight, gap } = SKELETON_GEOMETRY.metric;
  return (
    <div
      className="w-full"
      style={{ display: 'grid', gap: gap * 2, gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {Array.from({ length: rows * columns }, (_, index) => (
        <div key={index} className="flex flex-col" style={{ gap }}>
          <Skeleton height={labelHeight} width="52%" />
          <Skeleton height={figureHeight} width="74%" />
        </div>
      ))}
    </div>
  );
}

/**
 * `page` — a page's reserved header block over a table.
 *
 * This is what replaces `LoadingOverlay`. The difference that matters is that it
 * occupies the content region rather than covering it: nothing is dimmed, nothing
 * is blocked, and the shell, sidebar and top bar stay live and interactive
 * (Requirement 2.2, 14.2).
 */
function PageSkeleton({ rows, columns }) {
  const { headerHeight, gap } = SKELETON_GEOMETRY.page;
  return (
    <div className="flex w-full flex-col" style={{ gap }}>
      <div className="flex flex-col justify-center" style={{ height: headerHeight, gap: 8 }}>
        <Skeleton height={24} width="240px" />
        <Skeleton height={10} width="160px" />
      </div>
      <TableSkeleton rows={rows} columns={columns} />
    </div>
  );
}

/**
 * The loading affordance for one element.
 *
 * @param {Object} props
 * @param {string} props.kind One of {@link LOADING_KINDS}. Required — Requirement 14.2
 *   is about the indicator matching the element, and only the caller knows the shape.
 *   An absent or unrecognised kind throws in development and degrades to `inline`.
 * @param {number} [props.rows] Body rows (`skeleton-table`, `page`), card rows
 *   (`skeleton-cards`) or rows of figures (`skeleton-metric`). Ignored by
 *   `skeleton-chart`, `inline` and `button`.
 * @param {number} [props.columns] Columns (`skeleton-table`, `page`), cards per row
 *   (`skeleton-cards`) or figures across (`skeleton-metric`).
 * @param {string} [props.label] The accessible name for this load. Defaults to
 *   `'Loading'`; `ds/Panel` passes `Loading {title}` so a page with nine panels
 *   announces nine distinguishable loads rather than nine identical ones.
 * @param {string} [props.className]
 */
export function LoadingState({ kind, rows, columns, label, className = '', ...rest }) {
  const known = LOADING_KINDS.includes(kind);
  assertContract(
    known,
    `LoadingState: \`kind\` must be one of ${LOADING_KINDS.join(' | ')}, received ${JSON.stringify(kind)}. `
      + 'Requirement 14.2 asks for a loading shape that matches the element it replaces, '
      + 'and only the call site knows which that is.',
  );
  // Production fallback. `inline` is the one shape that is honest about any content:
  // it claims no dimensions, so it cannot introduce the layout shift a wrongly
  // guessed skeleton would.
  const resolved = known ? kind : 'inline';

  const defaults = DEFAULT_COUNTS[resolved];
  const rowCount = count(rows, defaults.rows);
  const columnCount = count(columns, defaults.columns);
  const accessibleName = hasText(label) ? label : DEFAULT_LABEL;

  let body;
  switch (resolved) {
    case 'skeleton-table':
      body = <TableSkeleton rows={rowCount} columns={columnCount} />;
      break;
    case 'skeleton-cards':
      body = <CardsSkeleton rows={rowCount} columns={columnCount} />;
      break;
    case 'skeleton-chart':
      body = <ChartSkeleton />;
      break;
    case 'skeleton-metric':
      body = <MetricSkeleton rows={rowCount} columns={columnCount} />;
      break;
    case 'page':
      body = <PageSkeleton rows={rowCount} columns={columnCount} />;
      break;
    case 'button':
      // Sized to a button's text line so a control does not resize while it works.
      body = <Spinner size={14} />;
      break;
    case 'inline':
    default:
      body = (
        <>
          <Spinner size={14} />
          <span className="text-body text-content-secondary">{accessibleName}</span>
        </>
      );
      break;
  }

  // `inline` and `button` show their affordance in flow; the skeletons are blocks.
  const layout =
    resolved === 'inline' || resolved === 'button'
      ? 'inline-flex items-center gap-2 text-content-secondary'
      : 'block w-full';

  return (
    <div
      role="status"
      aria-busy="true"
      data-loading-kind={resolved}
      data-loading-rows={rowCount}
      data-loading-columns={columnCount}
      className={`${layout} ${className}`.trim()}
      {...rest}
    >
      {/* `inline` already renders the label as visible text; every other kind needs
          the name for assistive technology only, since a shimmer says nothing. */}
      {resolved === 'inline' ? null : <span className="sr-only">{accessibleName}</span>}
      {body}
    </div>
  );
}

export default LoadingState;
