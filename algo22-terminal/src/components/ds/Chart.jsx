/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/Chart — the one chart, and the only module in `src/` that imports recharts
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.18. design.md §11.4, §13.3.
 * Requirements 1.5, 15.5, 18.1.
 * Property P30 (both axes labelled; legend exactly when multi-series).
 *
 * ┌───────────────────────────────────────────────────────────────────────┐
 * │ HOW TO IMPORT THIS FILE — it is NOT in `ds/index.js`, on purpose      │
 * └───────────────────────────────────────────────────────────────────────┘
 *
 * recharts is ~350KB before gzip and exactly three in-scope pages chart
 * (Dashboard, Portfolio, Backtester). Strategies, Trade History and Signal Trace
 * do not, and design.md §13.3 says they must not pay for it. A barrel re-export
 * would defeat that outright: `import { Panel } from '../components/ds'` would
 * pull recharts into every page that imports anything from `ds/`. So task 6.23's
 * barrel deliberately omits `Chart`, and the default export below is what makes
 * the alternative work.
 *
 *   // pages/Portfolio.jsx — the intended form
 *   import { lazy, Suspense } from 'react';
 *   import { LoadingState, Panel } from '../components/ds';
 *
 *   const Chart = lazy(() => import('../components/ds/Chart'));
 *
 *   <Panel
 *     title="Equity curve"
 *     state={equity.state}                     // from `usePanelState`
 *     loading={{ kind: 'skeleton-chart' }}
 *     empty={{ headline: 'No equity history',
 *              body: 'The curve appears once this account has a closed trade.',
 *              action: { label: 'View strategies', to: '/app/strategies' } }}
 *     error={{ error: equity.error, context: 'portfolio', onRetry: equity.refetch }}
 *   >
 *     <Suspense fallback={<LoadingState kind="skeleton-chart" label="Loading equity curve" />}>
 *       <Chart kind="area" data={equity.data} … />
 *     </Suspense>
 *   </Panel>
 *
 * Two details in there are load-bearing. `lazy()` needs a module whose `default`
 * is the component, which is why this file has one as well as the named export.
 * And the `Suspense` fallback is `skeleton-chart` at {@link SKELETON_GEOMETRY}'s
 * chart height — the same number this component lays out at — so the panel does
 * not resize when the chunk lands (Requirement 14.2).
 *
 * This file is meant to become the ONLY module in `src/` that imports recharts, and
 * it is not that yet. Three others still do it statically today — `pages/Backtester`,
 * `pages/PaperTrading` and `components/ResearchConsole` — and every one of them defeats
 * the split on its own, because a static import anywhere in the entry graph hoists
 * `vendor-recharts` into it regardless of what this file does. `vite.config.js` already
 * routes recharts to its own chunk; that only pays off once the last static importer is
 * gone. Those three call sites belong to tasks 23.1 and 25.1, and to no task at all in
 * the case of `ResearchConsole`, so `Chart.test.jsx` pins the current set instead: FOUR
 * entries, this file included. It fails the moment a FIFTH appears, and it fails again
 * when one is removed without the list coming down with it. Same ratchet as
 * `no-colour-literals`, for the same reason — progress that is not recorded does not
 * hold.
 *
 * The list was seven when task 6.18 wrote this, and three have left it since.
 * `pages/Portfolio` went at task 16.2, which moved its three charts onto a
 * `lazy(() => import(...))` of this file. `components/DashboardUpgrades` went at task
 * 19.4, which deleted that file outright — 786 lines of gamified upgrade prompts with no
 * importer (design.md §7.1, Requirement 1.5); that one is a real gain for the split
 * rather than a migration, because a whole module dropped out of the entry graph.
 * `pages/Dashboard` went at task 19.1b, which rebuilt tier 2 and put the equity curve
 * behind `lazy(() => import('../components/ds/Chart'))` and a `skeleton-chart` fallback,
 * exactly as the example above shows. That is the departure §13.3 cared about most:
 * Dashboard is `/app/dashboard`, and until task 20.1 gives it its own page,
 * `/app/live-trading` as well, so its static import was what put ~350KB of charting into
 * the graph of the three routes that draw no chart at all (Strategies, Trade History,
 * Signal Trace).
 *
 * ┌───────────────────────────────────────────────────────────────────────┐
 * │ WHAT IS STRUCTURAL HERE                                               │
 * └───────────────────────────────────────────────────────────────────────┘
 *
 * BOTH AXIS LABELS ARE REQUIRED PROPS (Requirement 15.5)
 * -----------------------------------------------------
 * `xAxis.label` and `yAxis.label` are not optional and there is no fallback. An
 * unlabelled axis is a column of numbers a trader has to guess at, and every
 * chart in the app today ships at least one: `Backtester`'s equity curve hides
 * both axes outright (`<XAxis hide />`), and `Portfolio`'s labels neither. Making
 * the label a required prop that throws in development is the difference between
 * a rule and a habit — see `ds/devAssert` for why it throws in dev and logs in
 * production.
 *
 * `legend="auto"` IS REQUIREMENT 15.5'S CONDITION, WRITTEN AS CODE
 * ---------------------------------------------------------------
 * "a legend when more than one series" is a sentence somebody has to remember at
 * each call site. {@link showsLegend} is the same sentence as a function of
 * `series.length`, evaluated once, here. `legend="auto"` (the default) shows the
 * legend if and only if there are two or more series — so a single-series chart
 * cannot grow a legend that repeats its own title, and a two-series chart cannot
 * ship without one. `legend={true}`/`legend={false}` remain available for a call
 * site that has a reason, and P30 asserts the `auto` biconditional in both
 * directions at 1 and 2 series.
 *
 * THE TOOLTIP IS REACHABLE BY KEYBOARD, NOT ONLY BY POINTER
 * --------------------------------------------------------
 * Requirement 15.5 asks for values "on hover/focus" and Requirement 18.1 asks for
 * every interaction to be keyboard-reachable. recharts' own tooltip is driven by
 * `mousemove` on the chart surface and by nothing else, so shipping it alone would
 * satisfy neither. This component adds a data cursor:
 *
 *   * A transparent, focusable overlay sits across the plot area with
 *     `role="slider"`. That role is the accurate one — the control's value is a
 *     position along the x axis, its interaction is the arrow keys, and it comes
 *     with `aria-valuetext`, which is how a screen reader says "Date 2024-03-11,
 *     Equity 12,400.50" instead of "3". It is also an interactive role, so the
 *     overlay is a legitimate tab stop rather than a `div` with a `tabIndex`
 *     (which is what task 6.27's `jsx-a11y-x` escalation exists to reject).
 *   * `pointer-events: none` keeps the overlay out of the pointer's way, so hover
 *     still reaches recharts underneath. Pointer-events do not affect keyboard
 *     focus, so Tab still lands on it. Two input methods, one cursor, no conflict.
 *   * ArrowRight/ArrowUp and ArrowLeft/ArrowDown step one point, Home/End jump to
 *     the ends, PageUp/PageDown move by {@link PAGE_STEP}. Each is clamped, so the
 *     cursor never leaves the data.
 *   * The focused point is announced through an `aria-live="polite"` region
 *     (design.md §11.4) and simultaneously carried on `aria-valuetext`. Both are
 *     deliberate and the cost is known: a screen reader that reads the slider's
 *     value change *and* the live region will say the point twice on an arrow
 *     press. The alternative is worse in each direction — dropping the live region
 *     loses the announcement on any change that is not a value change (a tick
 *     arriving under a held cursor), and dropping `aria-valuetext` leaves the
 *     control announcing a bare ordinal on focus, before any arrow key is pressed.
 *   * A vertical `ReferenceLine` marks the cursor on the plot and the tooltip
 *     panel follows it, so the keyboard path is visible to a sighted keyboard user
 *     rather than being an assistive-technology-only affordance.
 *
 * The live region is silent until the overlay is focused, so a page with nine
 * charts announces nothing on mount.
 *
 * NO GRADIENTS, NO GLOW (Requirement 1.5)
 * ---------------------------------------
 * Area fills are a flat `wash` token at full opacity. There is no `<defs>`, no
 * `<linearGradient>` and no `url(#…)` fill anywhere below. `MiniSparkline` in
 * `ui-legacy/primitives.jsx` builds a `sparkFill-` gradient per colour and both
 * charting pages build `eg`/`btEq` gradients inline; none of that is copied here.
 * Draw-in animation is off as well: a calm terminal does not redraw its equity
 * curve every time a panel re-renders, and it makes the rendered DOM
 * deterministic for the property tests.
 *
 * COLOUR COMES FROM A SERIES *TOKEN*, NEVER FROM A COLOUR PROP
 * -----------------------------------------------------------
 * `series[].token` names one of {@link SERIES_TOKENS} and {@link SERIES_PALETTE}
 * resolves it through `design/semantic.js`. There is no `color`/`stroke`/`fill`
 * prop on this component, so a page cannot introduce a seventh hue.
 *
 * `pnlToken` is deliberately NOT used to colour a series. It maps a *value* to
 * profit/loss, and applying it per point would make one line change colour along
 * its own length — a curve that is green where it rose and red where it fell reads
 * as two series. A series' hue is declared once, by the page, from what the series
 * *is* (`token: 'profit'` for realised P&L, `token: 'error'` for drawdown,
 * `token: 'brand'` for a neutral equity curve — design.md §11.4).
 *
 * AN EMPTY CHART IS THE PANEL'S EMPTY STATE, NOT EMPTY AXES
 * --------------------------------------------------------
 * This component never draws axes over nothing and never renders an empty state of
 * its own — `ds/EmptyState`, reached through `ds/Panel`, is the only thing that
 * knows how to say what is missing, why it matters and what to do next
 * (Requirement 14.1), and `ds/DataTable` already treats emptiness the same way.
 *
 * On a correctly written page this component simply never renders empty, because
 * `usePanelState` puts the panel in `empty` for a zero-length read
 * (`isEmptyPayload`) and `Panel` does not render children in that state. So an
 * empty `Chart` means the page skipped the state contract, and that is what the
 * assertion below reports — in development, by name, with the fix. Production
 * renders `emptyMessage` as a single line and no axes: less, and never a chart
 * shape implying data that is not there.
 *
 * {@link hasChartData} is exported because it is what "empty" means for a chart
 * specifically — a row array is not enough, the declared series keys have to hold
 * a finite number somewhere — and P30's generator needs the same predicate this
 * component uses rather than a second copy of it.
 *
 * `CustomTooltip`, RETOKENED
 * -------------------------
 * {@link ChartTooltip} is `ui-legacy/primitives.jsx`'s `CustomTooltip` with its
 * `C.bg2`/`C.border`/`C.t2`/`C.cyan` chrome moved onto token utilities, its
 * hardcoded `radius: 8` onto `rounded-md`, and its `$`-by-default `prefix` removed
 * — a `prefix="$"` default silently mislabels every non-USDT figure, and the axis
 * `format` now carries that information instead. The array-index `key` is replaced
 * by the series key. It is copied rather than imported: task 6.26 owns
 * `primitives.jsx` and this file must not touch it, and importing from the legacy
 * module would drag `C` and the whole primitive set into the lazy chunk.
 *
 * @module components/ds/Chart
 */

import { useCallback, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { statusToken } from '../../design/semantic';
import { token } from '../../design/tokens';

import { assertContract, hasText } from './devAssert';
import { SKELETON_GEOMETRY } from './LoadingState';

/* ══════════════════════════════════════════════════════════════════════════
 * THE VOCABULARY
 * ══════════════════════════════════════════════════════════════════════════ */

/** design.md §11.4. Three kinds, and no fourth without a design decision. */
export const CHART_KINDS = Object.freeze(['area', 'line', 'bar']);

/**
 * The series palette, resolved through `design/semantic.js`.
 *
 * `brand` is the neutral series hue and is the one entry that reads `token`
 * directly: `semantic.js` exposes brand only as a *stage band* fill
 * (`STAGE_BAND.TRANSFORM.fg`), and borrowing the Strategy Builder's lane colour to
 * mean "equity curve" would tie two unrelated decisions together. The value is the
 * same `--color-brand` either way, so no new colour enters the app.
 *
 * The rest are `statusToken` groups verbatim, which is what keeps a drawdown curve
 * the same red as a failed deployment badge.
 */
export const SERIES_PALETTE = Object.freeze({
  brand: Object.freeze({ group: 'brand', fg: token.brand.base, wash: token.brand.wash }),
  profit: Object.freeze(statusToken('profit')),
  loss: Object.freeze(statusToken('loss')),
  error: Object.freeze(statusToken('error')),
  warning: Object.freeze(statusToken('warning')),
  neutral: Object.freeze(statusToken('neutral')),
});

/** The token names a series may declare. Derived, so the two cannot diverge. */
export const SERIES_TOKENS = Object.freeze(Object.keys(SERIES_PALETTE));

/**
 * What an axis `format` may say. It chooses the tick text and the announcement
 * text, and nothing else — it never chooses a colour.
 */
export const AXIS_FORMATS = Object.freeze([
  'text',
  'number',
  'currency',
  'percent',
  'date',
  'datetime',
  'time',
]);

/** `legend` accepts these. `'auto'` is Requirement 15.5's condition; see {@link showsLegend}. */
export const LEGEND_MODES = Object.freeze(['auto', true, false]);

/** Above this many series, `legend="auto"` shows the legend. Requirement 15.5. */
export const LEGEND_THRESHOLD = 1;

/** The not-available marker, as everywhere else in `ds/` (design.md §3.2). */
export const NOT_AVAILABLE = '—';

/** Spoken form of {@link NOT_AVAILABLE}. A screen reader saying "em dash" tells nobody anything. */
const NOT_AVAILABLE_SPOKEN = 'not available';

/** PageUp / PageDown stride, in data points. */
export const PAGE_STEP = 10;

/**
 * How close to a plot edge the keyboard tooltip may be centred before it is pinned.
 *
 * Roughly the half-width of a two-series panel. Inside this margin the panel is
 * left- or right-aligned to the cursor instead of centred on it, so it stays inside
 * the card rather than hanging off the edge of the panel that contains it.
 */
const TOOLTIP_EDGE_GUARD = 96;

/**
 * Plot geometry.
 *
 * `height` is `SKELETON_GEOMETRY.chart.height`, imported rather than restated, so
 * `LoadingState`'s `skeleton-chart` and the real chart are the same height and the
 * panel does not resize when data arrives (Requirement 14.2).
 */
const MARGIN = Object.freeze({ top: 8, right: 12, bottom: 24, left: 4 });

/** The y-axis gutter. Wide enough for a grouped currency tick at `--text-micro`. */
const AXIS_WIDTH = 64;

/** The x-axis gutter, which holds a tick row and the axis label beneath it. */
const AXIS_HEIGHT = 40;

/**
 * The width used before, or instead of, a measurement.
 *
 * recharts needs a pixel width. `ResponsiveContainer` would supply one in a
 * browser and zero under jsdom — where it renders nothing at all, which would make
 * every assertion in P30 and in this component's unit tests vacuously true against
 * an empty SVG. So the width is measured here (see {@link useMeasuredWidth}) and
 * falls back to this, which keeps the component renderable anywhere and keeps the
 * tests testing something.
 */
const FALLBACK_WIDTH = 640;

/* ══════════════════════════════════════════════════════════════════════════
 * VALUE READING AND FORMATTING — local on purpose
 * ══════════════════════════════════════════════════════════════════════════ */

/*
 * `ds/DataTable` exports `toFiniteNumber`, `toEpochMs` and `formatCellValue`, and
 * these are near-copies of three of them. Importing them instead would put
 * `DataTable` — and through it `lucide-react` and `react-router-dom` — inside the
 * static import graph of the one module that is supposed to be dynamically
 * imported and to contain nothing but the chart. Tree-shaking would probably
 * recover most of it; "probably" is not a good enough basis for the one decision
 * this task exists to protect. The duplication is ~30 lines, is covered by tests
 * on both sides, and is the cheaper of the two mistakes.
 */

/** A finite number, or `null`. Decimal strings parse; booleans deliberately do not. */
function toFiniteNumber(value) {
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
 * A bare numeric string is not accepted: `'1700000000'` could be seconds or
 * milliseconds and guessing wrong moves a trade by fifty years.
 */
function toEpochMs(value) {
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

/** `row[key]`, own properties only, so a column keyed `toString` reads nothing. */
function readValue(row, key) {
  if (row === null || typeof row !== 'object') return undefined;
  if (typeof key !== 'string') return undefined;
  return Object.prototype.hasOwnProperty.call(row, key) ? row[key] : undefined;
}

/** `'12'`, `'-3.50'`, `'+0.7'` — a decimal that can be grouped without reinterpreting. */
const PLAIN_DECIMAL = /^[+-]?\d+(?:\.\d+)?$/;

/**
 * Thousands separators, added by hand and never rounding.
 *
 * Not `Intl.NumberFormat`, for `ds/DataTable`'s reasons: its separators are
 * locale-dependent, so a terminal showing `1.234,5` on one axis and `1,234.5` on
 * another has manufactured a hazard out of a formatting default, and
 * `maximumFractionDigits` would round a figure a trader is reading as money.
 * Exponential notation is returned untouched — grouping it would misstate its
 * magnitude.
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

/** A grouped decimal for `value`, or `null` when there is no honest number in it. */
function groupedNumber(value) {
  if (typeof value === 'string' && PLAIN_DECIMAL.test(value.trim())) {
    return groupDecimal(value.trim());
  }
  const numeric = toFiniteNumber(value);
  return numeric === null ? null : groupDecimal(String(numeric));
}

/**
 * A value rendered for an axis tick, a tooltip row or an announcement.
 *
 * Returns `null` for anything with no honest rendering — `null`, `undefined`, the
 * empty string, `NaN`, an unparseable date, an object. The caller substitutes the
 * not-available marker, never `0` and never a blank (Requirement 14.5).
 *
 * Times are UTC, to the second, in the order a trader scans. Local time would make
 * the same backtest read differently in two offices.
 *
 * @param {unknown} value
 * @param {string} [format] One of {@link AXIS_FORMATS}. Anything else reads as text.
 * @returns {string|null}
 */
export function formatAxisValue(value, format) {
  if (value === null || value === undefined) return null;

  if (format === 'date' || format === 'datetime' || format === 'time') {
    const epochMs = toEpochMs(value);
    if (epochMs === null) return null;
    const iso = new Date(epochMs).toISOString();
    if (format === 'date') return iso.slice(0, 10);
    if (format === 'time') return `${iso.slice(11, 19)}Z`;
    return `${iso.slice(0, 10)} ${iso.slice(11, 19)}Z`;
  }

  if (format === 'number' || format === 'currency') return groupedNumber(value);

  if (format === 'percent') {
    const grouped = groupedNumber(value);
    return grouped === null ? null : `${grouped}%`;
  }

  if (typeof value === 'string') return value.trim() === '' ? null : value;
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : null;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return null;
}

/** {@link formatAxisValue}, with the marker substituted. For visible text. */
function displayValue(value, format) {
  const text = formatAxisValue(value, format);
  return text === null ? NOT_AVAILABLE : text;
}

/** {@link formatAxisValue}, with the spoken marker substituted. For announcements. */
function spokenValue(value, format) {
  const text = formatAxisValue(value, format);
  return text === null ? NOT_AVAILABLE_SPOKEN : text;
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE THREE DECISIONS, AS FUNCTIONS
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * Requirement 15.5's legend condition.
 *
 * `'auto'` — the default and the only mode a page should normally use — is
 * `seriesCount > 1`, evaluated here rather than remembered at each call site. An
 * explicit `true`/`false` overrides it. Anything else is treated as `'auto'`
 * (the component asserts separately, so an unknown mode is reported *and* renders
 * the correct thing rather than falling into "no legend" silently).
 *
 * @param {'auto'|boolean|unknown} mode
 * @param {number} seriesCount
 * @returns {boolean}
 */
export function showsLegend(mode, seriesCount) {
  const count = Number.isFinite(seriesCount) ? seriesCount : 0;
  if (mode === true) return true;
  if (mode === false) return false;
  return count > LEGEND_THRESHOLD;
}

/**
 * Whether there is anything to plot.
 *
 * A non-empty `data` array is NOT enough. A chart whose declared series keys hold
 * no finite number anywhere draws labelled axes over nothing, which is the exact
 * outcome design.md §11.4 rules out — and it is also how a mistyped series key
 * presents, so treating it as empty turns a silent blank plot into a reported one.
 *
 * `0` counts. A bar chart of trade counts that are all zero has data
 * (Requirement 14.5: a zero is a value, not an absence).
 *
 * @param {unknown} data
 * @param {Array<{key: string}>} series
 * @returns {boolean}
 */
export function hasChartData(data, series) {
  if (!Array.isArray(data) || data.length === 0) return false;
  const keys = (Array.isArray(series) ? series : [])
    .map((entry) => (entry && typeof entry === 'object' ? entry.key : null))
    .filter((key) => typeof key === 'string' && key !== '');
  if (keys.length === 0) return false;
  return data.some((row) => keys.some((key) => toFiniteNumber(readValue(row, key)) !== null));
}

/**
 * The sentence a focused point is announced as.
 *
 * `"Date 2024-03-11. Equity 12,400.50. Drawdown not available. Point 3 of 90."`
 *
 * The x axis is named, not just its value, because "2024-03-11" alone does not say
 * what it measures. Every series is read whether or not it has a value at this
 * point, and a gap is spoken as "not available" rather than skipped — a series
 * that silently disappears from the announcement reads as a series that is not on
 * the chart.
 *
 * Exported for P30 and for the unit tests: the announcement is behaviour, not
 * decoration, so it is asserted directly rather than through the DOM.
 *
 * @param {Object} args
 * @param {Object} args.row The data row at the cursor.
 * @param {number} args.index Zero-based cursor position.
 * @param {number} args.count Total points.
 * @param {{key: string, label: string, format?: string}} args.xAxis
 * @param {{label: string, format?: string}} args.yAxis
 * @param {Array<{key: string, name: string}>} args.series
 * @returns {string}
 */
export function describePoint({ row, index, count, xAxis, yAxis, series }) {
  const parts = [`${xAxis.label} ${spokenValue(readValue(row, xAxis.key), xAxis.format)}`];
  (Array.isArray(series) ? series : []).forEach((entry) => {
    parts.push(`${entry.name} ${spokenValue(readValue(row, entry.key), yAxis.format)}`);
  });
  parts.push(`Point ${index + 1} of ${count}`);
  return `${parts.join('. ')}.`;
}

/* ══════════════════════════════════════════════════════════════════════════
 * NORMALISATION
 * ══════════════════════════════════════════════════════════════════════════ */

/** An axis descriptor with every optional field filled in once. */
function normaliseAxis(axis) {
  const source = axis && typeof axis === 'object' ? axis : {};
  return Object.freeze({
    key: typeof source.key === 'string' ? source.key : '',
    label: typeof source.label === 'string' ? source.label : '',
    format: AXIS_FORMATS.includes(source.format) ? source.format : 'text',
    domain: Array.isArray(source.domain) ? source.domain : undefined,
  });
}

/**
 * Series descriptors with their colours resolved.
 *
 * `name` falls back to `key` so a legend entry is never blank; `token` falls back
 * to `brand`, which is the neutral hue rather than a signed one — guessing
 * `profit` for an undeclared series would colour a drawdown curve green.
 */
function normaliseSeries(series) {
  const list = Array.isArray(series) ? series : [];
  return Object.freeze(
    list.map((entry) => {
      const source = entry && typeof entry === 'object' ? entry : {};
      const key = typeof source.key === 'string' ? source.key : '';
      const paletteName = SERIES_TOKENS.includes(source.token) ? source.token : 'brand';
      const palette = SERIES_PALETTE[paletteName];
      return Object.freeze({
        key,
        name: hasText(source.name) ? source.name : key,
        token: paletteName,
        colour: palette.fg,
        wash: palette.wash,
      });
    }),
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * MEASUREMENT
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The element's content width, or {@link FALLBACK_WIDTH} before/without a measurement.
 *
 * This replaces recharts' `ResponsiveContainer` rather than wrapping it, for the
 * reason given at {@link FALLBACK_WIDTH}: the container reports zero under jsdom
 * and recharts then renders no SVG at all, so every test and every property about
 * axis labels and legends would pass against nothing. `ResizeObserver` is used
 * when the environment has one and a `resize` listener otherwise, so the component
 * has no hard dependency on either.
 *
 * `useLayoutEffect` so the first paint already has the real width; a
 * `useEffect` here shows one frame at the fallback width and then reflows.
 */
function useMeasuredWidth(ref) {
  const [width, setWidth] = useState(0);

  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return undefined;

    const measure = () => setWidth(node.clientWidth || 0);
    measure();

    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(measure);
      observer.observe(node);
      return () => observer.disconnect();
    }
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [ref]);

  return width > 0 ? width : FALLBACK_WIDTH;
}

/**
 * Where the cursor at `index` sits, in pixels from the container's left edge.
 *
 * Category axes place a point at the centre of its band for `bar` and at the band
 * edge for `area`/`line`, which is why the two are computed differently: a
 * reference line drawn at the band edge of a bar chart lands between two bars.
 */
function cursorOffset(kind, index, count, width) {
  const plotLeft = MARGIN.left + AXIS_WIDTH;
  const plotRight = Math.max(plotLeft + 1, width - MARGIN.right);
  const span = plotRight - plotLeft;
  if (count <= 0) return plotLeft;
  if (kind === 'bar') return plotLeft + (span / count) * (index + 0.5);
  if (count === 1) return plotLeft + span / 2;
  return plotLeft + (span / (count - 1)) * index;
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE TOOLTIP — `CustomTooltip`, retokened
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The tooltip body, driven by recharts on hover and by the data cursor on focus.
 *
 * One body, two drivers. recharts clones this element with `{active, payload,
 * label}`; the keyboard path constructs the same three props by hand from the
 * cursor index. That is why the shape is recharts' rather than something tidier —
 * a second tooltip for the keyboard path is a second thing to keep in step, and
 * they would drift the first time a format changed.
 *
 * `aria-hidden` throughout: the values are already announced through the live
 * region and `aria-valuetext`, and a tooltip that appears and disappears under the
 * pointer is not something a screen reader should read a third time.
 *
 * @param {Object} props
 * @param {boolean} [props.active] recharts' hover flag.
 * @param {Array<{dataKey?: string, name?: string, value?: unknown, color?: string}>} [props.payload]
 * @param {unknown} [props.label] The x value at the hovered/focused point.
 * @param {string} props.xLabel The x axis's label, so the row says what it measures.
 * @param {string} [props.xFormat] One of {@link AXIS_FORMATS}.
 * @param {string} [props.yFormat] One of {@link AXIS_FORMATS}.
 */
export function ChartTooltip({ active, payload, label, xLabel, xFormat, yFormat }) {
  if (active !== true || !Array.isArray(payload) || payload.length === 0) return null;

  return (
    <div
      aria-hidden="true"
      data-chart-tooltip="true"
      className="pointer-events-none rounded-md border border-line-default bg-surface-raised px-3 py-2 shadow-raised"
    >
      <p className="mb-1 font-mono text-micro text-content-secondary">
        {`${xLabel} ${displayValue(label, xFormat)}`}
      </p>
      {payload.map((entry, index) => (
        <p
          // The series key, not the array index: a series toggled off mid-list
          // would otherwise re-key every row after it.
          key={entry.dataKey ?? entry.name ?? index}
          className="flex items-baseline justify-between gap-3 font-mono text-body tabular-nums"
          style={{ color: entry.color ?? token.content.primary }}
        >
          <span>{entry.name}</span>
          <span className="font-semibold">{displayValue(entry.value, yFormat)}</span>
        </p>
      ))}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE LEGEND
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The legend, as an HTML list above the plot.
 *
 * recharts' `<Legend>` is not used. It renders an unsemantic `<ul>` of `<li>`s with
 * inline colours it computes itself, inside the chart's own wrapper — so it takes
 * its colours out of this file's control, and it renders only when the SVG does.
 * A plain list here is a real list to assistive technology, takes its swatch colour
 * from {@link SERIES_PALETTE} like everything else, and is present whether or not
 * the plot measured — which is what lets P30 read it.
 *
 * The swatch is `aria-hidden` and the name is text, so the legend never depends on
 * colour alone to be read (Requirement 12.3's principle, applied here).
 */
function ChartLegend({ series }) {
  return (
    <ul
      data-chart-legend-list="true"
      className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1"
    >
      {series.map((entry) => (
        <li key={entry.key} className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            data-series-token={entry.token}
            className="inline-block h-0.5 w-3 rounded-full"
            style={{ backgroundColor: entry.colour }}
          />
          <span className="font-mono text-micro text-content-secondary">{entry.name}</span>
        </li>
      ))}
    </ul>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE COMPONENT
 * ══════════════════════════════════════════════════════════════════════════ */

const CHART_BY_KIND = Object.freeze({ area: AreaChart, line: LineChart, bar: BarChart });

/**
 * Shared tick presentation. `content.secondary` is 6.2:1 — axis ticks are text.
 *
 * Exported for `Chart.test.jsx` only. recharts is stubbed in every page test that
 * touches a chart, so an axis tick's size never reaches rendered SVG there: the
 * value's correctness is a property of this declaration and has to be asserted
 * over it (retail-ui-simplification task 3.1).
 */
export const TICK = Object.freeze({
  fill: token.content.secondary,
  // fontSize: 10 → --text-micro (axis tick: a label, per tokens.css:69's own
  // `labels, chips` annotation). retail-ui-simplification task 3.2.
  fontSize: token.text.micro,
  fontFamily: token.font.mono,
});

/** An axis label, in the same treatment as a tick but not monospaced. Exported per {@link TICK}. */
export const AXIS_LABEL_STYLE = Object.freeze({
  fill: token.content.secondary,
  // fontSize: 10 → --text-micro (axis label, same treatment as the tick).
  // retail-ui-simplification task 3.2.
  fontSize: token.text.micro,
  fontFamily: token.font.sans,
});

/** One plotted series, per kind. Flat fills, no gradient, no draw-in animation. */
function seriesMark(kind, entry) {
  const shared = {
    key: entry.key,
    dataKey: entry.key,
    name: entry.name,
    isAnimationActive: false,
  };

  if (kind === 'bar') {
    return <Bar {...shared} fill={entry.colour} radius={[2, 2, 0, 0]} />;
  }
  if (kind === 'area') {
    return (
      <Area
        {...shared}
        type="monotone"
        stroke={entry.colour}
        strokeWidth={2}
        // The flat wash at full opacity IS the Requirement 1.5 decision: a
        // `<linearGradient>` from `stopOpacity 0.3` to `0` is what this replaces.
        fill={entry.wash}
        fillOpacity={1}
        dot={false}
        // A gap in the data is drawn as a gap. `connectNulls` would draw a
        // straight line through a period the exchange reported nothing for.
        connectNulls={false}
      />
    );
  }
  return (
    <Line
      {...shared}
      type="monotone"
      stroke={entry.colour}
      strokeWidth={2}
      dot={false}
      connectNulls={false}
    />
  );
}

/**
 * The one chart.
 *
 * @param {Object} props
 * @param {'area'|'line'|'bar'} props.kind One of {@link CHART_KINDS}.
 * @param {Array<Object>} props.data The rows, in x order. The page sorts; this does not.
 * @param {{key: string, label: string, format?: string}} props.xAxis
 *   `key` and `label` are both REQUIRED. `label` is Requirement 15.5.
 * @param {{label: string, format?: string, key?: string, domain?: Array}} props.yAxis
 *   `label` is REQUIRED. `key` is informational only — series keys carry the values.
 * @param {Array<{key: string, name?: string, token?: string}>} props.series
 *   Non-empty. `token` names one of {@link SERIES_TOKENS}; there is no colour prop.
 * @param {'auto'|boolean} [props.legend] Default `'auto'` — see {@link showsLegend}.
 * @param {boolean} [props.tooltip] Default `true`. Suppresses the tooltip PANEL only;
 *   the keyboard cursor and its announcement are Requirement 18.1 and are not optional.
 * @param {string} [props.emptyMessage] The production fallback line when there is no
 *   data. In development an empty chart throws instead — the panel owns emptiness.
 * @param {number} [props.height] Plot height. Defaults to `skeleton-chart`'s.
 * @param {string} [props.className]
 */
export function Chart({
  kind,
  data,
  xAxis,
  yAxis,
  series,
  legend = 'auto',
  tooltip = true,
  emptyMessage,
  height = SKELETON_GEOMETRY.chart.height,
  className = '',
  ...rest
}) {
  const captionId = useId();
  const hintId = useId();
  const containerRef = useRef(null);
  const width = useMeasuredWidth(containerRef);

  const [cursor, setCursor] = useState(0);
  const [focused, setFocused] = useState(false);

  const x = useMemo(() => normaliseAxis(xAxis), [xAxis]);
  const y = useMemo(() => normaliseAxis(yAxis), [yAxis]);
  const marks = useMemo(() => normaliseSeries(series), [series]);

  const rows = Array.isArray(data) ? data : [];
  const count = rows.length;

  // ── The contract ────────────────────────────────────────────────────────

  const knownKind = CHART_KINDS.includes(kind);
  assertContract(
    knownKind,
    `Chart: \`kind\` must be one of ${CHART_KINDS.join(' | ')}, received ${JSON.stringify(kind)}.`,
  );
  // An unrecognised kind falls to `line`, which is the shape that asserts least
  // about the data: an area implies a magnitude worth filling under and a bar
  // implies discrete categories, and either could be wrong.
  const resolvedKind = knownKind ? kind : 'line';

  // Requirement 15.5, the half that is a required prop. Asserted per axis so the
  // message names the one that is missing.
  assertContract(
    hasText(x.label),
    'Chart: `xAxis.label` is REQUIRED (Requirement 15.5). An unlabelled axis is a row of '
      + 'values a trader has to guess the meaning of. Pass `xAxis={{ key, label, format }}`.',
  );
  assertContract(
    hasText(y.label),
    `Chart (${x.label || 'unlabelled'}): \`yAxis.label\` is REQUIRED (Requirement 15.5). `
      + 'Name the quantity and its unit — "Equity (USDT)", not "Equity".',
  );
  assertContract(
    hasText(x.key),
    `Chart (${y.label || 'unlabelled'}): \`xAxis.key\` is required — it names the field the `
      + 'points are placed along. Without it every point sits at the same x.',
  );
  assertContract(
    marks.length > 0,
    `Chart (${y.label || 'unlabelled'}): \`series\` must be a non-empty array of `
      + '`{ key, name, token }`. A chart declares what it plots; it does not infer it from the '
      + 'first row, which is how a series disappears the moment one row omits a field.',
  );
  assertContract(
    LEGEND_MODES.includes(legend),
    `Chart (${y.label || 'unlabelled'}): \`legend\` must be 'auto', true or false, received `
      + `${JSON.stringify(legend)}. 'auto' is Requirement 15.5's condition and is what a page `
      + 'should normally pass.',
  );
  marks.forEach((entry) => {
    assertContract(
      hasText(entry.key),
      `Chart (${y.label || 'unlabelled'}): every series needs a \`key\` naming its field.`,
    );
  });

  // ── Emptiness belongs to the panel, not to this component ───────────────
  const populated = hasChartData(rows, marks);
  assertContract(
    populated,
    `Chart (${y.label || 'unlabelled'}) has nothing to plot: ${count} row(s), and no finite `
      + `value under ${marks.map((entry) => `\`${entry.key}\``).join(', ') || 'any series key'}. `
      + 'A chart must not draw labelled axes over nothing (design.md §11.4). Emptiness is the '
      + "enclosing `Panel`'s state, not this component's: drive it from `usePanelState`, which "
      + 'already reports `empty` for a zero-length read, and give the panel an '
      + '`empty={{ headline, body, action }}` so `EmptyState` can say what is missing and what '
      + 'to do next (Requirement 14.1). If the rows are there, a series `key` does not match '
      + 'the field names in `data`.',
  );

  // ── The data cursor ─────────────────────────────────────────────────────

  // Derived, not stored. Data shrinking under a held cursor must not leave the
  // cursor pointing past the end, and clamping in an effect would render one frame
  // out of range first.
  const index = count > 0 ? Math.min(Math.max(cursor, 0), count - 1) : 0;
  const row = rows[index];

  const announcement = useMemo(
    () =>
      (populated && row !== undefined
        ? describePoint({ row, index, count, xAxis: x, yAxis: y, series: marks })
        : ''),
    [populated, row, index, count, x, y, marks],
  );

  const step = useCallback(
    (delta) => {
      setCursor((previous) => {
        const from = Math.min(Math.max(previous, 0), Math.max(0, count - 1));
        return Math.min(Math.max(from + delta, 0), Math.max(0, count - 1));
      });
    },
    [count],
  );

  const handleKeyDown = useCallback(
    (event) => {
      // Right/Up increase and Left/Down decrease, which is the slider contract for
      // `aria-orientation="horizontal"` and is what a screen reader user expects
      // the role to do. Each is prevented because each would otherwise scroll the
      // page out from under the chart.
      switch (event.key) {
        case 'ArrowRight':
        case 'ArrowUp':
          event.preventDefault();
          step(1);
          break;
        case 'ArrowLeft':
        case 'ArrowDown':
          event.preventDefault();
          step(-1);
          break;
        case 'Home':
          event.preventDefault();
          setCursor(0);
          break;
        case 'End':
          event.preventDefault();
          setCursor(Math.max(0, count - 1));
          break;
        case 'PageUp':
          event.preventDefault();
          step(PAGE_STEP);
          break;
        case 'PageDown':
          event.preventDefault();
          step(-PAGE_STEP);
          break;
        default:
          // Everything else bubbles. Escape in particular: `ds/Drawer` and
          // `ds/ConfirmDialog` close on it, and a chart inside one must not
          // swallow that.
          break;
      }
    },
    [count, step],
  );

  // ── Render ──────────────────────────────────────────────────────────────

  const legendShown = showsLegend(legend, marks.length);

  // The accessible summary of the chart. SVG `<text>` is not reliably exposed as an
  // accessible name, so the axis labels recharts draws are for sighted readers and
  // this sentence is what assistive technology gets. Both come from the same two
  // required props, so they cannot disagree.
  const caption = `${y.label} by ${x.label}. `
    + `${resolvedKind} chart, ${marks.length} series (${marks.map((entry) => entry.name).join(', ')}), `
    + `${count} data point${count === 1 ? '' : 's'}.`;

  /*
   * The attributes both the populated and the fallback render carry.
   *
   * `data-chart-*` mirrors `ds/DataTable`'s `data-align` / `data-column-key`
   * precedent: P30 is a statement about two axis labels and a legend, and reading
   * them off attributes is what lets the property assert the *decision* rather than
   * scraping SVG text and hoping recharts' internal markup does not change.
   */
  const shell = {
    'data-chart-kind': resolvedKind,
    'data-chart-series-count': String(marks.length),
    'data-chart-legend': legendShown ? 'true' : 'false',
    'data-chart-x-label': x.label,
    'data-chart-y-label': y.label,
    'data-chart-empty': populated ? 'false' : 'true',
    className: `m-0 flex min-w-0 flex-col ${className}`.trim(),
    ...rest,
  };

  if (!populated) {
    // Production fallback only — development threw above. One line of prose, no
    // axes, no chart shape implying data that is not there.
    return (
      <figure {...shell}>
        <figcaption id={captionId} className="sr-only">{caption}</figcaption>
        <p className="px-4 py-8 text-center text-body text-content-secondary">
          {hasText(emptyMessage) ? emptyMessage : 'No data for this period.'}
        </p>
      </figure>
    );
  }

  const ChartByKind = CHART_BY_KIND[resolvedKind];
  const cursorX = cursorOffset(resolvedKind, index, count, width);
  const plotLeft = MARGIN.left + AXIS_WIDTH;
  const plotRight = Math.max(plotLeft + 1, width - MARGIN.right);
  // Keep the panel inside the plot at the extremes instead of letting it hang off
  // the edge of the card.
  let panelTransform = 'translateX(-50%)';
  if (cursorX - plotLeft < TOOLTIP_EDGE_GUARD) panelTransform = 'translateX(0)';
  else if (plotRight - cursorX < TOOLTIP_EDGE_GUARD) panelTransform = 'translateX(-100%)';

  const cursorPayload = marks.map((entry) => ({
    dataKey: entry.key,
    name: entry.name,
    value: readValue(row, entry.key),
    color: entry.colour,
  }));

  return (
    <figure {...shell}>
      <figcaption id={captionId} className="sr-only">{caption}</figcaption>

      {/* Requirement 15.5: exactly when multi-series, decided by `showsLegend`. */}
      {legendShown ? <ChartLegend series={marks} /> : null}

      <div ref={containerRef} className="relative w-full" style={{ height }}>
        <ChartByKind width={width} height={height} data={rows} margin={MARGIN}>
          {/* Horizontal rules only. A full grid on a 90-point equity curve is
              more ink than information. `line.subtle` is a non-text token. */}
          <CartesianGrid stroke={token.line.subtle} vertical={false} />

          <XAxis
            dataKey={x.key}
            height={AXIS_HEIGHT}
            stroke={token.line.default}
            tick={TICK}
            tickLine={false}
            minTickGap={24}
            tickFormatter={(value) => displayValue(value, x.format)}
            label={{
              value: x.label,
              position: 'insideBottom',
              offset: 0,
              style: AXIS_LABEL_STYLE,
            }}
          />

          <YAxis
            width={AXIS_WIDTH}
            domain={y.domain ?? ['auto', 'auto']}
            stroke={token.line.default}
            tick={TICK}
            tickLine={false}
            tickFormatter={(value) => displayValue(value, y.format)}
            label={{
              value: y.label,
              angle: -90,
              position: 'insideLeft',
              style: { ...AXIS_LABEL_STYLE, textAnchor: 'middle' },
            }}
          />

          {tooltip ? (
            <Tooltip
              // recharts drives the hover path with this; the keyboard path
              // renders the same body itself, below.
              content={
                <ChartTooltip xLabel={x.label} xFormat={x.format} yFormat={y.format} />
              }
              cursor={{ stroke: token.line.strong, strokeWidth: 1 }}
              isAnimationActive={false}
            />
          ) : null}

          {/* The keyboard cursor, drawn on the plot so a sighted keyboard user can
              see where it is. Only while focused — a line parked at point 0 on
              every chart on the page would read as a marker that means something. */}
          {focused ? (
            <ReferenceLine
              x={readValue(row, x.key)}
              stroke={token.brand.base}
              strokeWidth={1}
              isFront
            />
          ) : null}

          {marks.map((entry) => seriesMark(resolvedKind, entry))}
        </ChartByKind>

        {/* THE DATA CURSOR (Requirements 15.5, 18.1).
            `role="slider"` because the value is a position along the x axis and the
            interaction is the arrow keys — and because it is an interactive role,
            so this is a real tab stop rather than a `div` carrying a `tabIndex`.
            `pointer-events-none` leaves hover to recharts underneath without
            affecting keyboard focus. */}
        <div
          role="slider"
          tabIndex={0}
          data-chart-cursor="true"
          aria-labelledby={captionId}
          aria-describedby={hintId}
          aria-orientation="horizontal"
          aria-valuemin={0}
          aria-valuemax={Math.max(0, count - 1)}
          aria-valuenow={index}
          aria-valuetext={announcement}
          onKeyDown={handleKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          className="pointer-events-none absolute inset-0 rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
        />

        {/* The tooltip on the keyboard path. Same body as the hover path. */}
        {tooltip && focused ? (
          <div
            className="pointer-events-none absolute top-0 z-10 w-max"
            style={{ left: `${cursorX}px`, transform: panelTransform }}
          >
            <ChartTooltip
              active
              payload={cursorPayload}
              label={readValue(row, x.key)}
              xLabel={x.label}
              xFormat={x.format}
              yFormat={y.format}
            />
          </div>
        ) : null}
      </div>

      <p id={hintId} className="sr-only">
        {`Use the arrow keys to move a cursor across the ${count} data points. `
          + 'Home and End jump to the first and last point; Page Up and Page Down move by '
          + `${PAGE_STEP}.`}
      </p>

      {/* design.md §11.4: the focused point is announced. Empty until the cursor is
          focused, so a page with several charts announces nothing on mount. */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        data-chart-announcement="true"
        className="sr-only"
      >
        {focused ? announcement : ''}
      </div>
    </figure>
  );
}

export default Chart;
