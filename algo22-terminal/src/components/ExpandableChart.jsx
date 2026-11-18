/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ExpandableChart.jsx — click a graph, read it large (CHART-MODAL-01)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Task 06. The Backtester already draws useful charts; what it could not do is let a trader
 * inspect one. A 240px-tall equity curve over a multi-year run compresses hundreds of points
 * into a band a few pixels per week, and the only way to read it was to squint.
 *
 * This renders a chart inline exactly as before, adds one control that opens the SAME chart
 * in a modal at readable height, and leaves the page underneath untouched.
 *
 * ---------------------------------------------------------------------------
 * WHY THIS TAKES CHART PROPS AND NOT A CHART ELEMENT
 * ---------------------------------------------------------------------------
 * The obvious API would be `<ExpandableChart><Chart … /></ExpandableChart>`, cloning the
 * child with a bigger `height` for the modal. That is how the duplicate-instance problem
 * Task 06 warns about gets created: the element is constructed by the caller on every render
 * of the page, whether the modal is open or not, and `cloneElement` gives you a second one
 * that shares the first's props object.
 *
 * Taking the props as data means exactly one `<Chart>` exists at a time per surface — the
 * inline one — and the modal's is CONSTRUCTED ON OPEN and UNMOUNTED ON CLOSE. There is no
 * second instance to leak, no cloned props to drift, and one description of the chart rather
 * than two that could disagree about its series or its axes.
 *
 * `ds/Chart` sizes itself from its container through `useMeasuredWidth`, so the modal copy
 * needs no width plumbing: it measures the dialog and redraws. Only `height` is passed
 * explicitly, because that is the one dimension a chart cannot infer from a flexible parent.
 *
 * ---------------------------------------------------------------------------
 * HOW THE ENLARGED VIEW ZOOMS — A WINDOW OVER THE ROWS, NOT A `<Brush>`
 * ---------------------------------------------------------------------------
 * Task 06 asks for an enlarged, ZOOMABLE view. A taller chart is not a zoomable one: 2,000
 * equity points in 520px is 0.26px per point, so the detail a trader opened the dialog to
 * read is still not there. Three mechanisms were available and the third is what this uses.
 *
 *   1. **recharts' `<Brush>`.** The idiomatic answer, and it has to be a CHILD of the
 *      `AreaChart`/`LineChart`/`BarChart` element — which lives inside `ds/Chart`, takes no
 *      children, and is the one module in `src/` allowed to import recharts. Adding zoom
 *      this way means adding a prop to the shared chart primitive and changing what it
 *      renders for Dashboard and Portfolio too, from a feature commit, for a surface those
 *      pages did not ask about. The brush strip also costs ~40px of the plot height and
 *      re-draws the whole series in miniature underneath itself, which is the opposite of
 *      the trade this dialog exists to make.
 *   2. **Wheel / pinch scaling.** Discussed and rejected below.
 *   3. **A window over the row array — this file.** `view` is a `{start, span}` range of
 *      INDICES, and the dialog's chart is handed `rows.slice(start, end + 1)`. That is
 *      exactly what a `<Brush>` does internally (`startIndex`/`endIndex` window the data
 *      recharts plots); doing it from outside gets the same mechanism without touching the
 *      primitive or the other two pages.
 *
 * Windowing the DATA rather than the x DOMAIN is not a preference either. `ds/Chart` draws
 * its `<XAxis>` from `dataKey` with recharts' default `type="category"`, and a category axis
 * has no numeric domain to narrow — `xAxis.domain` is normalised by `ds/Chart` and then only
 * ever applied to the Y axis. There is no prop on the primitive today that could express
 * "show me points 1200 to 1400", so the window is the only honest route to one.
 *
 * What falls out of it, which is the reason to prefer it rather than a consequence to excuse:
 *
 *   * **Labels and values cannot drift.** `ds/Chart` derives every tick, every tooltip row
 *     and every announcement from the rows it is given, so a windowed chart is a chart of
 *     the window — correct by construction at any zoom level, with no second code path that
 *     could format a zoomed tick differently from an unzoomed one.
 *   * **Zooming in gets CHEAPER, not dearer.** Deeper zoom hands recharts fewer points, so
 *     it draws fewer SVG nodes. A 5,000-point run costs one `slice` per gesture (memoised,
 *     so re-renders that do not move the window recompute nothing) and the full-domain case
 *     passes the caller's own array with no copy at all.
 *   * **The inline card is untouched.** The window is modal state and the inline chart is
 *     memoised, so it does not even re-render when the window moves. Task 06's requirement
 *     is an enlarged zoomable view; the 240px card stays the glanceable thing it was.
 *
 * ---------------------------------------------------------------------------
 * WHY THERE IS NO WHEEL OR PINCH ZOOM
 * ---------------------------------------------------------------------------
 * Three reasons, in the order they matter:
 *
 *   * **It is undiscoverable.** Nothing on screen says a gesture exists. Task 06 asks for a
 *     control a trader can see, and a visible, labelled control is strictly more capable
 *     here anyway — it is the same control a keyboard user and a screen-reader user get,
 *     rather than a mouse-only affordance with a keyboard path bolted on beside it.
 *   * **It fights the dialog's own scrolling.** The dialog scrolls when the viewport is
 *     shorter than the chart (`overflowY: auto`, below). A wheel handler that zooms has to
 *     `preventDefault()` to stop that scroll, which means a non-passive listener on a
 *     scrolling surface — the browser then cannot thread-scroll the dialog at all, and the
 *     trader loses the ability to reach the sentence under the chart with the wheel.
 *   * **Wheel events arrive in bursts.** A single trackpad flick is 20–50 `wheel` events;
 *     one `setState` each is 20–50 React renders and 20–50 recharts re-draws of a 2,000-point
 *     area path. Coalescing them (rAF, or a ref plus a trailing commit) is possible and is
 *     code that exists only to undo a hazard the discrete controls never create. The buttons
 *     below change the window once per intent, which is the rate the data can be read at.
 *
 * ---------------------------------------------------------------------------
 * THE PAGE BEHIND THE DIALOG DOES NOT SCROLL
 * ---------------------------------------------------------------------------
 * `hooks/useScrollLock` owns that, including the two parts that are easy to get wrong — a
 * reference count, so this dialog and `landing/Navbar`'s menu cannot clobber each other's
 * restore, and paying the vanished scrollbar's width back as padding so the results page
 * does not jump sideways behind the backdrop. The whole of this file's involvement is
 * `useScrollLock(open)`: the claim follows the component, so an unmount while open releases
 * it just as closing does.
 *
 * ---------------------------------------------------------------------------
 * WHAT IS DELIBERATELY NOT REBUILT HERE
 * ---------------------------------------------------------------------------
 * Escape-to-close, the focus trap, focus return to the opener and hiding the background from
 * assistive technology all come from `hooks/useFocusTrap`, which `ds/ConfirmDialog` already
 * uses. Mutual exclusion with other overlays comes from `ds/overlayRegistry`, which THROWS in
 * development if two open at once — so this cannot quietly stack on top of a confirm dialog.
 *
 * Nothing is added to `ds/` from here. A shared chart-modal primitive may well be worth
 * having, but adding one from a feature commit is how a design system acquires a control
 * nobody reviewed — the same call `NotificationSwitch` records for not adding a switch.
 */

import { Suspense, lazy, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronLeft, ChevronRight, Maximize2, RotateCcw, X, ZoomIn, ZoomOut } from 'lucide-react';

import { CommandButton } from './ds/CommandButton';
import { LoadingState } from './ds/LoadingState';
import { OVERLAY_KIND, releaseOverlay, requestOverlay } from './ds/overlayRegistry';
import { useFocusTrap } from '../hooks/useFocusTrap';
import { useScrollLock } from '../hooks/useScrollLock';
import { token, cssVar } from '../design/tokens';

/**
 * `ds/Chart`, LAZILY — and this is not a style choice.
 *
 * `Chart.jsx` is the only module in `src/` permitted to import recharts (~350 KB before
 * gzip), and `pages/Backtester.jsx` imports it through `lazy()` for exactly that reason.
 * An eager `import { Chart }` here would undo that: this component is imported eagerly by
 * the page, so recharts would be pulled into the page's chunk whether a chart is drawn or
 * not, and the split `vite.config.js` maintains for it would stop doing anything.
 *
 * Both render sites below are wrapped in `Suspense` with the same `skeleton-chart` fallback
 * the page already used, so the loading behaviour is unchanged as well as the bundle.
 *
 * It is also why nothing is imported from `ds/Chart` by VALUE. `formatAxisValue` and
 * `hasChartData` are exported there and would be useful to {@link describeWindow} and
 * {@link windowHasValue} below, and a static `import { hasChartData } from './ds/Chart'`
 * would hoist recharts into the page's chunk just as surely as importing the component
 * would. The two small predicates are restated here instead — the same trade `ds/Chart`
 * itself makes for `ds/DataTable`'s three formatters, and for the same reason.
 */
const Chart = lazy(() => import('./ds/Chart'));

/**
 * The modal chart's height.
 *
 * A fixed figure rather than a viewport fraction. `ds/Chart` takes a pixel `height`, and a
 * `vh`-derived number would have to be recomputed on resize by this component — re-rendering
 * the chart on every pixel of a window drag. 60% of a 900px window is about this, and the
 * dialog scrolls if the viewport is shorter.
 */
const MODAL_CHART_HEIGHT = 520;

/**
 * The narrowest window the zoom will produce, in data points.
 *
 * A floor rather than a limit for its own sake: below about this many points an area or line
 * chart stops being a curve and becomes a scatter of segments, the x axis thins to two or
 * three ticks, and `ds/Chart`'s `minTickGap` starts dropping the labels that say where you
 * are. Eight is also enough that the floor is never the thing a trader hits first — three
 * zoom steps from 500 points lands at 63, five at 16.
 */
export const MIN_VISIBLE_POINTS = 8;

/** Each zoom step halves or doubles the window. Doubling is the conventional step and the
 *  one that makes the keyboard and pointer paths agree with each other exactly. */
export const ZOOM_STEP = 2;

/**
 * A pan moves by this fraction of the window — half of it.
 *
 * Zoom without pan is zoom into the midpoint and nowhere else, which would leave the end of
 * a 5,000-point run permanently unreadable; that is why the two pan controls exist at all.
 * Half a window keeps a shared edge between the view before the pan and the view after it,
 * so a drawdown that straddles the boundary is not cut in half by the gesture that was
 * supposed to bring it into view.
 */
export const PAN_FRACTION = 2;

/**
 * The stand-in for a chart with no `data`.
 *
 * One frozen array rather than a fresh `[]` per render. A new identity every render would
 * re-run every memo below for nothing, and — worse — would make the window's data-identity
 * check (see `request`) compare two arrays that are never the same object, so a dataless
 * chart would discard its own window on every keystroke.
 */
const NO_ROWS = Object.freeze([]);

/* ══════════════════════════════════════════════════════════════════════════
 * THE WINDOW, AS PURE FUNCTIONS
 * ══════════════════════════════════════════════════════════════════════════
 *
 * These are exported for the same reason `useFocusTrap.collectFocusable` and
 * `ds/Chart.describePoint` are: they are the half of the zoom that is arithmetic, every
 * off-by-one lives in them, and asserting them directly is cheaper and far more exact than
 * inferring them from a rendered SVG.
 */

/** `value`, rounded, inside `[low, high]`. A non-finite input reads as `low`. */
function clamp(value, low, high) {
  if (!Number.isFinite(value)) return low;
  return Math.min(Math.max(Math.round(value), low), high);
}

/**
 * `row[key]`, own properties only — so a series keyed `toString` reads nothing.
 *
 * `ds/Chart.readValue`, restated. See the note on the `lazy()` import above.
 */
function readValue(row, key) {
  if (row === null || typeof row !== 'object') return undefined;
  if (typeof key !== 'string' || key === '') return undefined;
  return Object.prototype.hasOwnProperty.call(row, key) ? row[key] : undefined;
}

/**
 * Whether a cell holds a number a chart could plot.
 *
 * `ds/Chart.toFiniteNumber`'s acceptance rule, as a predicate: numbers must be finite, a
 * decimal string parses, and everything else — `null`, `''`, a boolean, an object — is a
 * gap. The two have to agree, because this decides which windows are offered and that one
 * decides what gets drawn.
 */
function isPlottable(value) {
  if (typeof value === 'number') return Number.isFinite(value);
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed !== '' && Number.isFinite(Number(trimmed));
  }
  return false;
}

/**
 * The visible window, clamped to the data it is applied to.
 *
 * Derived on every render rather than stored, which is `ds/Chart`'s rule for its data cursor
 * and holds here for the same reason: a backtest that is re-run while the dialog is open
 * replaces the rows underneath the window, and a stored `{start: 1800, span: 200}` against a
 * 90-point result would index past the end. Clamping on the way out means the invalid state
 * is never rendered, where clamping in an effect would render it once and then correct it.
 *
 * @param {{start: number, span: number}|null|undefined} request What the controls asked for.
 *   `null` means the full domain — see {@link zoomRequest} on why that is a distinct value
 *   rather than `{start: 0, span: count}`.
 * @param {number} count How many rows there are.
 * @returns {{start: number, end: number, span: number}} `end` is INCLUSIVE. `span` is `0`
 *   only when there is no data at all.
 */
export function resolveWindow(request, count) {
  const total = Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
  if (total === 0) return Object.freeze({ start: 0, end: 0, span: 0 });

  // `Math.min` rather than a bare floor: a 3-row chart cannot show 8 points, and a `clamp`
  // whose low bound exceeds its high bound returns the high one and reads as a bug later.
  const floor = Math.min(MIN_VISIBLE_POINTS, total);
  const span = clamp(Number.isFinite(request?.span) ? request.span : total, floor, total);
  const start = clamp(Number.isFinite(request?.start) ? request.start : 0, 0, total - span);
  return Object.freeze({ start, end: start + span - 1, span });
}

/**
 * The window `factor` times as wide as `view`, centred where `view` was centred.
 *
 * Centred, not anchored left: a trader zooming in is reading something in the middle of what
 * they can see, and an anchored zoom walks that subject off the right-hand edge.
 *
 * @param {{start: number, span: number}} view The window now.
 * @param {number} count How many rows there are.
 * @param {number} factor `0.5` to zoom in one step, `2` to zoom out one step.
 * @returns {{start: number, span: number}|null} `null` for the full domain. That is a
 *   distinct value and not a tidiness: it is what lets the dialog pass the caller's own row
 *   array rather than a `slice` of all of it, so zooming all the way back out is identical
 *   to never having zoomed — same array, same identity, no copy.
 */
export function zoomRequest(view, count, factor) {
  const total = Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
  if (total === 0) return null;

  const floor = Math.min(MIN_VISIBLE_POINTS, total);
  const span = clamp(view.span * factor, floor, total);
  if (span >= total) return null;

  // Centres, not edges: both are `(n - 1) / 2` from their start, which keeps a window of
  // even span from drifting half a point left on every step.
  const centre = view.start + (view.span - 1) / 2;
  return Object.freeze({ start: clamp(centre - (span - 1) / 2, 0, total - span), span });
}

/**
 * The window moved by half its own width, without changing its width.
 *
 * Clamped at both ends rather than wrapped: an equity curve has a first point and a last
 * point, and a pan that jumps from the end of the run back to the start would read as the
 * data having changed.
 *
 * @param {{start: number, span: number}} view
 * @param {number} count
 * @param {-1|1} direction `-1` towards the start of the run, `1` towards the end.
 * @returns {{start: number, span: number}|null} `null` when the whole domain is already
 *   visible and there is nothing to pan.
 */
export function panRequest(view, count, direction) {
  const total = Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
  if (total === 0 || view.span >= total) return null;
  const stride = Math.max(1, Math.round(view.span / PAN_FRACTION));
  return Object.freeze({
    start: clamp(view.start + direction * stride, 0, total - view.span),
    span: view.span,
  });
}

/**
 * Whether a window has anything for `ds/Chart` to draw.
 *
 * This is the one hazard windowing introduces, and it is worth stating plainly: `ds/Chart`
 * treats a chart with no finite value under any series key as a CALLER BUG — it throws in
 * development and renders `emptyMessage` in production — because for every other call site
 * that condition means the page skipped `usePanelState`'s empty state. Narrowing a window is
 * the one way a caller can create that condition out of data that is genuinely not empty:
 * `lib/drawdownSeries` derives `null` for an equity point it could not read, and a run with
 * a long unreadable stretch has windows inside it that hold only gaps.
 *
 * So a narrowing transition that would land on such a window is refused and the view stays
 * where it is (see `reframe`), rather than zooming into a dev-time crash or a chart that
 * says "no data" about a run that has plenty. The full domain is never tested and never
 * refused — whatever the caller passed is the caller's to answer for, exactly as before.
 *
 * @param {Array<Object>} rows
 * @param {string[]} keys The series keys, which is where a chart's values live.
 * @param {{start: number, end: number}} view
 * @returns {boolean}
 */
export function windowHasValue(rows, keys, view) {
  if (!Array.isArray(rows) || !Array.isArray(keys) || keys.length === 0) return false;
  for (let i = view.start; i <= view.end && i < rows.length; i += 1) {
    for (const key of keys) {
      if (isPlottable(readValue(rows[i], key))) return true;
    }
  }
  return false;
}

/**
 * An x value rendered for the status line, or `null` when it has no honest rendering.
 *
 * Deliberately narrower than `ds/Chart.formatAxisValue`, which it cannot import (see the
 * `lazy()` note above). The axis format is not consulted: the status line reports the EDGES
 * of the window, and the two formats in use on the Backtester are `date` — already an ISO
 * string in the row — and `number`, both of which render as themselves. Anything this cannot
 * render honestly is omitted from the sentence rather than guessed at, so the line can never
 * disagree with the axis beneath it.
 */
function edgeText(value) {
  if (value instanceof Date) {
    const time = value.getTime();
    return Number.isFinite(time) ? value.toISOString().slice(0, 10) : null;
  }
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : null;
  if (typeof value === 'string') return value.trim() === '' ? null : value.trim();
  return null;
}

/**
 * What the status line says, and what a screen reader hears when the window moves.
 *
 * The point range is always there and the x range is added when it can be rendered, because
 * "points 121 to 240 of 480" is the fact a trader needs to re-orient and "2024-03-11 to
 * 2024-06-02" is the one they need to act on. Exported and asserted directly: this sentence
 * is the discoverability of the whole feature, not decoration.
 *
 * @param {{view: {start: number, end: number, span: number}, rows: Array<Object>, xKey: string}} args
 * @returns {string}
 */
export function describeWindow({ view, rows, xKey }) {
  const total = Array.isArray(rows) ? rows.length : 0;
  if (total === 0 || view.span === 0) return 'No points to show.';

  const first = edgeText(readValue(rows[view.start], xKey));
  const last = edgeText(readValue(rows[Math.min(view.end, total - 1)], xKey));
  const range = first !== null && last !== null && first !== last ? `, ${first} to ${last}` : '';

  if (view.span >= total) return `All ${total} point${total === 1 ? '' : 's'}${range}.`;
  return `Points ${view.start + 1}–${view.end + 1} of ${total}${range}.`;
}

/** Whether a key press is somebody typing, rather than a zoom shortcut. */
function isTextEntry(target) {
  if (!target || target.nodeType !== 1) return false;
  if (target.isContentEditable === true) return true;
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

/* ══════════════════════════════════════════════════════════════════════════
 * THE COMPONENT
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * A chart with an inline presentation and an enlarged, zoomable one.
 *
 * @param {object} props
 * @param {string} props.title The chart's name, used as the dialog's accessible name and in
 *   the expand control's label. Required: a dialog with no name, and a second identical
 *   "Enlarge" button on a page with three charts, are the same accessibility defect.
 * @param {object} props.chart Props forwarded to `ds/Chart`, minus `height`. The single
 *   description of this chart — the inline and modal renders both read it.
 * @param {number} [props.height] Inline height. `ds/Chart`'s own default when omitted.
 * @param {React.ReactNode} [props.children] Rendered inside the dialog beneath the chart, for
 *   the sentence a panel uses to say where its figures come from.
 */
export function ExpandableChart({ title, chart, height, children }) {
  const instanceId = useId();
  const titleId = `${instanceId}-title`;
  const statusId = `${instanceId}-zoom-status`;
  const [open, setOpen] = useState(false);
  /**
   * The requested window, or `null` for the full domain.
   *
   * It carries the row array it was requested against. That is what makes "the data changed
   * underneath the window" decidable without an effect: a request whose `rows` is no longer
   * the array being rendered is a request about a different backtest, and it is discarded on
   * the spot rather than clamped into a plausible-looking window over unrelated numbers.
   */
  const [request, setRequest] = useState(null);
  const dialogRef = useRef(null);
  const closeRef = useRef(null);

  const rows = Array.isArray(chart?.data) ? chart.data : NO_ROWS;
  const count = rows.length;
  const xKey = typeof chart?.xAxis?.key === 'string' ? chart.xAxis.key : '';
  const declaredSeries = chart?.series;
  const seriesKeys = useMemo(
    () => (Array.isArray(declaredSeries) ? declaredSeries : [])
      .map((entry) => (entry && typeof entry === 'object' ? entry.key : null))
      .filter((key) => typeof key === 'string' && key !== ''),
    [declaredSeries],
  );

  const live = request !== null && request.rows === rows ? request : null;
  const view = resolveWindow(live, count);
  const isFullRange = view.span >= count;

  /*
    The rows the enlarged chart draws.

    At the full domain this IS the caller's array — not a copy of it — so the common case
    allocates nothing and the inline and enlarged charts demonstrably describe the same data.
    Memoised on the window's bounds rather than on `view`, which is a fresh object every
    render: a re-render that did not move the window must not re-slice 5,000 rows.
  */
  const visibleRows = useMemo(
    () => (isFullRange ? rows : rows.slice(view.start, view.end + 1)),
    [rows, isFullRange, view.start, view.end],
  );

  /*
    Zoom is offered only when a window could actually be narrower than the data. Below the
    floor every request clamps straight back to the full domain, and five controls that
    cannot change anything are worse than none — `ds/CommandButton` would also require a
    `disabledReason` on each of them (Requirement 15.3), which is five sentences explaining
    arithmetic rather than a state a trader can act on.
  */
  const zoomable = count > MIN_VISIBLE_POINTS;

  // ── Overlay registration ──────────────────────────────────────────────────
  useEffect(() => {
    if (!open) return undefined;
    requestOverlay({ id: instanceId, kind: OVERLAY_KIND.DIALOG, label: title });
    return () => releaseOverlay(instanceId);
  }, [open, instanceId, title]);

  /*
    The page behind the backdrop holds still while the dialog is open. `hooks/useScrollLock`
    owns the whole of it — the reference count that keeps this dialog and `landing/Navbar`'s
    menu from clobbering each other's restore, and the scrollbar-width padding that stops the
    results page shifting sideways. Keyed off `open`, so an unmount while open releases the
    lock through the effect's cleanup rather than relying on the close button being clicked.
  */
  useScrollLock(open);

  const close = useCallback(() => {
    setOpen(false);
    // The next open starts at the full domain. A dialog that reopened at yesterday's zoom
    // would be showing a window chosen for a question that has already been answered, and
    // the trader has no way of telling the window apart from the whole run at a glance.
    setRequest(null);
  }, []);

  /**
   * Move the window, through a pure transition.
   *
   * The transition runs inside the updater so it reads the window that is actually current,
   * not one captured when the handler was created — and so the five controls, the keyboard
   * shortcuts and any future caller all go through one place that resolves, transforms,
   * re-clamps and vets.
   */
  const reframe = useCallback(
    (transform) => {
      setRequest((previous) => {
        const held = previous !== null && previous.rows === rows ? previous : null;
        const next = transform(resolveWindow(held, count), count);
        // The full domain is the caller's own data and is never vetted or refused.
        if (next === null) return null;
        const settled = resolveWindow(next, count);
        if (!windowHasValue(rows, seriesKeys, settled)) return previous;
        return { rows, start: settled.start, span: settled.span };
      });
    },
    [rows, count, seriesKeys],
  );

  const zoomIn = useCallback(() => reframe((v, c) => zoomRequest(v, c, 1 / ZOOM_STEP)), [reframe]);
  const zoomOut = useCallback(() => reframe((v, c) => zoomRequest(v, c, ZOOM_STEP)), [reframe]);
  const panEarlier = useCallback(() => reframe((v, c) => panRequest(v, c, -1)), [reframe]);
  const panLater = useCallback(() => reframe((v, c) => panRequest(v, c, 1)), [reframe]);
  const resetZoom = useCallback(() => setRequest(null), []);

  /*
    `returnFocusTo` is NOT passed. `useFocusTrap` records what held focus when the trap
    engaged and hands it back on release, which is the opener by construction — and that is
    more reliable than a ref of our own, because `ds/CommandButton` is a plain function
    component with no `forwardRef`, so a `ref` on it would be null and focus would return
    nowhere. The close control below is a real `<button>` for the same reason: the trap needs
    a node it can actually focus.
  */
  useFocusTrap({
    active: open,
    containerRef: dialogRef,
    // The close control, not the chart: `ds/Chart` is keyboard-navigable and taking focus
    // straight into its data cursor would skip past the way out.
    initialFocusRef: closeRef,
    onEscape: close,
  });

  /*
    ── Zoom from the keyboard ───────────────────────────────────────────────

    The five controls are real buttons, so Tab and Enter already reach every one of them;
    this is an accelerator, not the keyboard path. Three decisions in it:

      * **On `document`, not on the dialog element.** `useFocusTrap` listens here for
        Escape and this follows it, for its reason and one more. The reason: focus is
        trapped inside the dialog while it is open, so a listener bound for exactly that
        time sees every key the dialog could receive — including keys pressed while focus
        has dropped to `<body>` because a control disabled itself. The extra one: a
        `role="dialog"` element is non-interactive, and hanging `onKeyDown` on it is
        precisely what `jsx-a11y-x/no-noninteractive-element-interactions` rejects — a rule
        this project holds at error rather than waiving (see `eslint.config.js`).
      * **Escape is not handled here at all.** The trap closes the dialog on it, which it
        goes on doing at every zoom level. Reset-on-Escape would be the worst of both: a
        trader pressing Escape to leave a modal would instead still be in it.
      * **The arrow keys belong to `ds/Chart`.** Its data cursor steps on Arrow, Home, End
        and PageUp/PageDown. Those events reach this listener after React's own handlers
        have run, with `defaultPrevented` already set — which is what the first guard reads,
        so a pan could never be stolen from a cursor move. `+`, `-` and `0` collide with
        nothing and are what every map and image viewer uses.

    The effect re-subscribes if a handler identity changes, which costs one listener swap and
    is why nothing here is read through a ref: unlike the focus trap, re-engaging mid-dialog
    has no observable effect.
  */
  useEffect(() => {
    if (!open || !zoomable) return undefined;

    const onKeyDown = (event) => {
      if (event.defaultPrevented || isTextEntry(event.target)) return;
      switch (event.key) {
        case '+':
        case '=':
          zoomIn();
          break;
        case '-':
        case '_':
          zoomOut();
          break;
        case '0':
          resetZoom();
          break;
        default:
          return;
      }
      // Only the keys that were acted on are consumed; everything else is left for the
      // chart, the trap and the page.
      event.preventDefault();
    };

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, zoomable, zoomIn, zoomOut, resetZoom]);

  /*
    The inline chart, memoised.

    Not a micro-optimisation: without it every zoom step re-renders the 240px card as well as
    the enlarged chart, and recharts re-walks a 5,000-point path to produce an identical SVG.
    Memoising the ELEMENT (rather than wrapping `ds/Chart` in `React.memo`, which would change
    a shared primitive for every page) lets React bail out of that subtree entirely, because
    the element it is handed is reference-identical to the one it already rendered.
  */
  const inlineChart = useMemo(
    () => (
      <Suspense fallback={<LoadingState kind="skeleton-chart" label={`Loading ${title}`} />}>
        <Chart {...chart} height={height} />
      </Suspense>
    ),
    [chart, height, title],
  );

  return (
    <>
      <div className="relative">
        {inlineChart}
        {/*
          The control is a real button beside the chart, not a click handler on the chart
          itself. `ds/Chart` owns its own pointer and keyboard interaction for the data
          cursor, so swallowing clicks there would mean a trader exploring the series could
          not stop a modal opening under them — and a `<div onClick>` over a chart is
          unreachable by keyboard, which is the half Task 06's accessibility requirement is
          about.
        */}
        <div className="absolute right-0 top-0">
          <CommandButton
            intent="ghost"
            icon={Maximize2}
            aria-label={`Enlarge ${title}`}
            data-testid="chart-expand"
            onClick={() => setOpen(true)}
          >
            Enlarge
          </CommandButton>
        </div>
      </div>

      {open
        ? createPortal(
          <div
            data-ds="chart-modal-backdrop"
            style={{
              position: 'fixed',
              inset: 0,
              zIndex: cssVar('z.modal'),
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: 'var(--spacing-6)',
              background: token.surface.overlay,
            }}
          >
            <div
              ref={dialogRef}
              role="dialog"
              aria-modal="true"
              aria-labelledby={titleId}
              data-testid="chart-modal"
              data-ds-overlay={OVERLAY_KIND.DIALOG}
              style={{
                width: '100%',
                // Wide, but not edge-to-edge: a chart that runs to the viewport's margins has
                // no quiet space and reads worse than one that stops short of it.
                maxWidth: 1100,
                // The dialog, not the page, scrolls when the viewport is shorter than the
                // chart — which is what keeps the backdrop covering the page behind it.
                maxHeight: 'calc(100dvh - 2 * var(--spacing-6))',
                overflowY: 'auto',
                background: token.surface.panel,
                border: `1px solid ${token.line.default}`,
                borderRadius: 'var(--radius-lg)',
                padding: 'var(--spacing-5)',
                display: 'flex',
                flexDirection: 'column',
                gap: 'var(--spacing-4)',
              }}
            >
              <div className="flex items-start justify-between gap-4">
                <h2
                  id={titleId}
                  className="text-section"
                  style={{ margin: 0, color: token.content.primary }}
                >
                  {title}
                </h2>
                {/*
                  A real `<button>`, not `ds/CommandButton`, for one reason: the focus trap
                  needs a node it can focus and `CommandButton` has no `forwardRef`. Every
                  colour is a token, so the colour guard is satisfied; `aria-label` carries
                  the chart's name so three modals on one page do not all announce "Close".
                */}
                <button
                  ref={closeRef}
                  type="button"
                  aria-label={`Close ${title}`}
                  data-testid="chart-modal-close"
                  onClick={close}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-line-default px-2.5 py-1.5 text-body text-content-secondary hover:bg-surface-raised hover:text-content-primary"
                >
                  <X size={14} aria-hidden="true" />
                  Close
                </button>
              </div>

              {zoomable ? (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  {/*
                    What is on screen, in words, and the live region that says so when it
                    changes. `ds/Chart` has a live region of its own for the focused point,
                    and the two cannot talk over each other in practice: the chart's is silent
                    until its cursor is focused, and this one speaks only on a zoom or a pan.

                    It is visible text and not `sr-only` because it is also what makes the
                    zoom legible to everybody else — a chart of an unlabelled sub-range is a
                    chart a trader can misread with complete confidence.
                  */}
                  <p
                    id={statusId}
                    role="status"
                    aria-live="polite"
                    aria-atomic="true"
                    data-testid="chart-zoom-status"
                    data-zoom-start={String(view.start)}
                    data-zoom-span={String(view.span)}
                    data-zoom-count={String(count)}
                    className="m-0 min-w-0 font-mono text-small text-content-secondary"
                  >
                    {describeWindow({ view, rows, xKey })}
                  </p>

                  {/*
                    `role="group"`, not `role="toolbar"`. A toolbar carries an ARIA contract
                    these five controls do not honour — one tab stop for the set, with the
                    arrow keys moving between its members — and the arrow keys already belong
                    to `ds/Chart`'s data cursor inside this dialog. Five ordinary tab stops is
                    the honest description, and the group's label is what keeps them from
                    being announced as five unexplained buttons.
                  */}
                  <div
                    role="group"
                    aria-label={`Zoom and pan ${title}`}
                    // The group is described by the status line, so entering the controls
                    // says what is currently on screen rather than leaving a screen-reader
                    // user to zoom blind out of a range they were never told.
                    aria-describedby={statusId}
                    data-testid="chart-zoom-controls"
                    className="flex shrink-0 flex-wrap items-center gap-2"
                  >
                    <CommandButton
                      intent="ghost"
                      icon={ChevronLeft}
                      data-testid="chart-pan-earlier"
                      onClick={panEarlier}
                    >
                      Back
                    </CommandButton>
                    <CommandButton
                      intent="ghost"
                      icon={ZoomOut}
                      data-testid="chart-zoom-out"
                      onClick={zoomOut}
                    >
                      Zoom out
                    </CommandButton>
                    <CommandButton
                      intent="ghost"
                      icon={ZoomIn}
                      data-testid="chart-zoom-in"
                      onClick={zoomIn}
                    >
                      Zoom in
                    </CommandButton>
                    <CommandButton
                      intent="ghost"
                      icon={ChevronRight}
                      data-testid="chart-pan-later"
                      onClick={panLater}
                    >
                      Forward
                    </CommandButton>
                    <CommandButton
                      intent="secondary"
                      icon={RotateCcw}
                      data-testid="chart-zoom-reset"
                      onClick={resetZoom}
                    >
                      Reset zoom
                    </CommandButton>
                  </div>
                </div>
              ) : null}

              {/*
                One `<Chart>`, built here, from the same props the inline one reads. It exists
                only while this branch is mounted, so closing the dialog disposes of it.

                `data` is the one prop overridden, and it is overridden AFTER the spread so the
                window cannot be silently undone by a caller that also passes `data` — the
                window is this component's state and nothing else may supply it. `ds/Chart`
                derives and clamps its own cursor from `data.length`, so narrowing the window
                under a held cursor moves the cursor into range rather than past the end.
              */}
              <Suspense
                fallback={<LoadingState kind="skeleton-chart" label={`Loading ${title}`} />}
              >
                <Chart {...chart} data={visibleRows} height={MODAL_CHART_HEIGHT} />
              </Suspense>

              {zoomable ? (
                <p className="sr-only">
                  {'Press plus to zoom in, minus to zoom out and zero to show the whole range. '
                    + 'Escape closes this dialog at any zoom level.'}
                </p>
              ) : null}

              {children}
            </div>
          </div>,
          document.body,
        )
        : null}
    </>
  );
}

export default ExpandableChart;
