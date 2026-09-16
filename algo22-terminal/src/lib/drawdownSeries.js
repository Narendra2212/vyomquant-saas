/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/lib/drawdownSeries.js — the drawdown curve, derived from the real equity curve
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 23.3. `design.md` §7.4. Requirements 6.3, 14.5.
 *
 * WHY THIS IS A DERIVATION AND NOT A CHART SHAPE
 * ---------------------------------------------
 * Requirement 6.3 asks for an equity curve and a drawdown curve side by side. The
 * backtest result carries the first (`results.charts.equity_curve`, read by
 * `api/modules/strategies.js::equitySeriesFromBacktestResults`) and carries no series
 * for the second — §7.4's table marks the drawdown curve ⚠️ for exactly that reason.
 *
 * So the curve has to be computed, and the whole question is whether what gets drawn is
 * a *function of a real series* or a plausible-looking shape. Putting the arithmetic
 * here, apart from the page and under a unit test, is what makes that checkable: every
 * point out is the running peak of the points in, minus the point itself, and there is
 * no branch anywhere in this file that produces a number the equity curve did not imply.
 *
 * ABSOLUTE, NOT PERCENTAGE — AND WHY THAT SETTLES THE ZERO-PEAK QUESTION
 * ---------------------------------------------------------------------
 * {@link absoluteDrawdownSeries} reports **peak − current in the equity curve's own
 * units** (§7.4's "running peak minus current, per point"). The name says so, because
 * `10000` and `10` are both credible drawdowns for the same run and only the unit tells
 * a reader which one is on screen.
 *
 * The percentage form, `(peak − current) / peak`, was not chosen, and the reason is what
 * it does at the boundaries rather than taste:
 *
 *   * A running peak of `0` — the first point of a curve that starts at zero, or a run
 *     that went to nothing — makes the quotient undefined. The absolute form has no
 *     division and no special case there: `0 − (−500)` is `500`, which is the true depth.
 *   * A negative running peak flips the quotient's sign, so a deepening loss would be
 *     reported as a *negative* drawdown. The absolute form cannot produce a negative
 *     value at all (see the invariant below), whatever sign the equity has.
 *   * The percentage figure the page needs already exists upstream: tier 1's max drawdown
 *     is the engine's own `max_drawdown_pct` (§7.4). Deriving a second percentage here
 *     would put two numbers on the same screen that can disagree.
 *
 * Two invariants follow from the running peak being taken *including* the current point:
 * the drawdown of a readable point is **never negative**, and it is **exactly `0` at a
 * high-water mark** (a point whose equity equals the running peak) and positive elsewhere.
 *
 * AN EMPTY EQUITY CURVE DERIVES AN EMPTY SERIES
 * --------------------------------------------
 * No points in, no points out. Not a flat line, not two points at the initial capital,
 * not a zero at each end of the requested period. Requirement 14.5 forbids substituting
 * fabricated values for strategy performance, and a flat drawdown line reads as the
 * strongest claim this chart can make — "this run never lost money" — from a record that
 * said nothing at all. The live defect this redesign exists to remove is precisely that
 * shape: paper mode synthesising an equity curve pinned at `100000.0` when QuestDB
 * returns no rows. This module cannot reproduce it, because its output length is the
 * input length and its values are the input's arithmetic.
 *
 * AN UNREADABLE POINT IS CARRIED, NOT DROPPED, AND NEVER READS AS ZERO
 * -------------------------------------------------------------------
 * A point whose equity is absent, `null`, `NaN`, `Infinity` or wrongly typed is emitted
 * with `readable: false` and `drawdown: null`. Three consequences, each deliberate:
 *
 *   1. **It is not `0`.** A zero drawdown is the claim "no loss from the peak here",
 *      which is the one thing an unreadable equity cannot support, and it is the claim
 *      that would understate the run's risk.
 *   2. **It is not dropped.** Dropping closes the gap: the chart would join its
 *      neighbours with a straight line, the series would be shorter than the equity
 *      curve it is paired with, and nothing on screen would say the record had a hole.
 *      Carried, `drawdown: null` breaks the line at that x — a visible gap — and the two
 *      tier-2 charts still have the same number of points.
 *   3. **It does not advance the running peak.** An unreadable value cannot be shown to
 *      be a new high, so it is not treated as one. It also is not carried forward from
 *      the previous point, which would be inventing an equity.
 *
 * That last choice has a residual cost worth naming rather than hiding: if an unreadable
 * point *was* in fact the run's high-water mark, every later depth is measured from a
 * lower peak and is therefore a lower bound on the true one. The alternatives are worse —
 * reading it as `0` equity fabricates a total wipeout, and reading it as the last known
 * equity fabricates a value the record does not contain. So the derivation stays a lower
 * bound and says so: {@link countUnreadableDrawdownPoints} reports how many points could
 * not be read, and {@link REASON_UNREADABLE_EQUITY} is the sentence the page shows when
 * that count is not zero. An incomplete curve labelled incomplete is honest; an
 * incomplete curve drawn as complete is not.
 *
 * A numeric *string* counts as unreadable here, for the same reason
 * `lib/signalTraceStages.js`'s reader does: the mapper upstream already coerces the
 * wire's numbers, so a string arriving at this module means something published a shape
 * nobody promised, and "cannot read this" beats a guess.
 *
 * WHAT IT READS
 * -------------
 * Either shape the backtest curve comes in, matching
 * `equitySeriesFromBacktestResults`: a list of `{timestamp, equity}` records (what the
 * mapper produces), or a list of bare numbers (what `BacktestRuntime` publishes under
 * `charts.equity_curve.values`), in which case the index is the timestamp. A record with
 * no usable `timestamp` also falls back to its index, so every emitted point has an x.
 *
 * ⚠️ One caveat for a caller passing the *mapped* series: the mapper reads
 * `Number(value.equity ?? 0)`, so an equity the wire omitted has already become `0` by
 * the time it arrives here and is indistinguishable from a genuine zero-equity point.
 * That coercion overstates the depth rather than understating it, and it is the mapper's
 * to fix, not this module's — recorded here so the next reader does not go looking for a
 * missing-value branch that could not possibly fire.
 *
 * WHAT THIS MODULE DOES NOT DO
 * ----------------------------
 * No maximum-drawdown figure: tier 1's is the engine's (§7.4), and a second one computed
 * here could contradict it. No axis labels, no formatting, no units string, no colour, no
 * React, no network, no clock, no module state. Every export answers for every input —
 * `null`, a string, an object, an array of garbage — nothing throws, and every returned
 * value is frozen.
 *
 * @module lib/drawdownSeries
 */

/* ══════════════════════════════════════════════════════════════════════════
 * The one sentence about incompleteness, stated once
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * What the page says when {@link countUnreadableDrawdownPoints} is not zero. Stated here
 * so the caveat is worded once and cannot drift between the two charts that need it.
 */
export const REASON_UNREADABLE_EQUITY =
  'Some points of this equity curve could not be read, so the drawdown curve has a gap '
  + 'at each of them rather than a zero, and the depths shown after a gap are measured '
  + 'from the highest equity that could be read.';

/* ══════════════════════════════════════════════════════════════════════════
 * Total readers
 * ══════════════════════════════════════════════════════════════════════════ */

/** A plain record, or `null`. An array is not a record and neither is a string. */
const asRecord = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null;

/** A finite number, or `null`. `NaN`, `Infinity` and numeric strings are not numbers. */
const asFiniteNumber = (value) =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

/**
 * The equity of one input point, or `null` when it cannot be read.
 *
 * Both published shapes: a bare number is the equity itself, a record carries it under
 * `equity`. Anything else — `null`, a string, an array, a record whose `equity` is absent
 * or not a finite number — is unreadable, which is a third answer and not a zero.
 *
 * @param {unknown} point
 * @returns {number|null}
 */
const equityOf = (point) => {
  if (typeof point === 'number') return asFiniteNumber(point);
  const record = asRecord(point);
  return record === null ? null : asFiniteNumber(record.equity);
};

/**
 * The x of one input point: its own `timestamp` where it has a usable one, otherwise its
 * index. Positional indices are how the bare-number form is paired with time upstream,
 * and a point with no x at all could not be drawn.
 *
 * @param {unknown} point
 * @param {number} index
 * @returns {string|number}
 */
const timestampOf = (point, index) => {
  const record = asRecord(point);
  if (record === null) return index;
  const stamp = record.timestamp;
  if (typeof stamp === 'number' && Number.isFinite(stamp)) return stamp;
  if (typeof stamp === 'string' && stamp.trim() !== '') return stamp;
  return index;
};

/* ══════════════════════════════════════════════════════════════════════════
 * The derivation
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * One derived point.
 *
 * @typedef {Object} DrawdownPoint
 * @property {string|number} timestamp The input point's own x, or its index.
 * @property {number|null} equity The equity that was read, or `null` when it could not be.
 * @property {number|null} peak The running peak over the readable points up to and
 *   including this one. `null` only before the first readable point.
 * @property {number|null} drawdown `peak - equity`, in the equity curve's own units.
 *   Never negative, `0` exactly at a high-water mark, and `null` — never `0` — where the
 *   equity could not be read.
 * @property {boolean} readable Whether this point's equity was a finite number.
 */

/**
 * The absolute drawdown series of an equity curve: running peak minus current, per point.
 *
 * The output has exactly one point per input point, in input order. An empty or
 * unreadable input derives an empty series rather than a flat one (Requirement 14.5), and
 * a point whose equity cannot be read derives `drawdown: null` rather than `0` — see the
 * module header for both decisions and for why absolute rather than percentage.
 *
 * Pure: no clock, no network, no module state, and `equitySeries` is not mutated.
 *
 * @param {unknown} equitySeries The mapped equity series
 *   (`{timestamp, equity}[]`), a list of bare equity numbers, or anything at all.
 * @returns {ReadonlyArray<DrawdownPoint>} Frozen, and frozen point by point.
 */
export function absoluteDrawdownSeries(equitySeries) {
  const points = Array.isArray(equitySeries) ? equitySeries : [];

  /** The high-water mark over readable points only. `null` until one is read. */
  let peak = null;

  return Object.freeze(
    points.map((point, index) => {
      const timestamp = timestampOf(point, index);
      const equity = equityOf(point);

      if (equity === null) {
        // The peak is deliberately left where it was: an unreadable value is not
        // evidence of a new high, and carrying the previous equity forward would
        // invent one.
        return Object.freeze({ timestamp, equity: null, peak, drawdown: null, readable: false });
      }

      // Taking the peak over the current point too is what makes the drawdown
      // non-negative by construction, and exactly `0` at a high-water mark.
      peak = peak === null ? equity : Math.max(peak, equity);

      return Object.freeze({ timestamp, equity, peak, drawdown: peak - equity, readable: true });
    }),
  );
}

/**
 * How many points of a derived series could not be read. `0` for an empty series and for
 * anything that is not one, so a caller can compare it to zero without checking first.
 *
 * The page uses this to decide whether to show {@link REASON_UNREADABLE_EQUITY}: the
 * curve is a lower bound whenever this is not zero, and the reader is entitled to know.
 *
 * @param {unknown} series The output of {@link absoluteDrawdownSeries}, or anything.
 * @returns {number}
 */
export function countUnreadableDrawdownPoints(series) {
  if (!Array.isArray(series)) return 0;
  return series.filter((point) => asRecord(point)?.readable !== true).length;
}
