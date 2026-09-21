/**
 * `lib/drawdownSeries.js` — vyomquant-ui-redesign task 23.3. design.md §7.4.
 * Requirements 6.3, 14.5.
 *
 * The two cases the task names — a monotonic-rising series derives all zeros, a
 * peak-then-trough series derives the exact trough depth — plus the honest-absence cases
 * that make this a derivation rather than a chart shape: an empty curve derives an empty
 * series (not a flat line at the initial capital), and a point whose equity cannot be read
 * derives `null` rather than a zero that would read as "no loss from the peak here".
 *
 * Input shapes are the backtest curve's own, as `api/modules/strategies.js
 * ::equitySeriesFromBacktestResults` reads them: `{timestamp, equity}` records, or bare
 * numbers paired positionally.
 */

import { describe, expect, it } from 'vitest';

import {
  REASON_UNREADABLE_EQUITY,
  absoluteDrawdownSeries,
  countUnreadableDrawdownPoints,
} from '../../../src/lib/drawdownSeries';

/** The mapped equity series shape, from a list of equities. */
const curve = (...equities) =>
  equities.map((equity, index) => ({ timestamp: `2024-01-${String(index + 1).padStart(2, '0')}`, equity }));

const drawdowns = (series) => series.map((point) => point.drawdown);

const readability = (series) => series.map((point) => point.readable);

// ---------------------------------------------------------------------------
// The two cases the task names
// ---------------------------------------------------------------------------

describe('absoluteDrawdownSeries: the shape of a real curve', () => {
  it('derives all zeros from a monotonic-rising series', () => {
    // Every point is its own high-water mark, so peak === equity throughout.
    const series = absoluteDrawdownSeries(curve(100000, 101000, 104500, 104500.75, 120000));

    expect(drawdowns(series)).toEqual([0, 0, 0, 0, 0]);
    expect(readability(series)).toEqual([true, true, true, true, true]);
    expect(series.map((point) => point.peak)).toEqual([100000, 101000, 104500, 104500.75, 120000]);
  });

  it('derives the exact trough depth from a peak-then-trough series', () => {
    // Peak 110000, trough 92500: the deepest point is 17500 below the high-water mark,
    // and each point on the way down is its own exact distance from that same peak.
    const series = absoluteDrawdownSeries(curve(100000, 110000, 104000, 92500, 96000));

    expect(drawdowns(series)).toEqual([0, 0, 6000, 17500, 14000]);
    expect(series.every((point) => point.peak === Math.max(100000, point.peak))).toBe(true);
    expect(series[3]).toEqual({
      timestamp: '2024-01-04',
      equity: 92500,
      peak: 110000,
      drawdown: 17500,
      readable: true,
    });
  });

  it('measures later depths from a new high once the curve recovers past the old one', () => {
    // The running peak resets at 118000, so the 8000 at the end is measured from the new
    // high and not from the 110000 that preceded the first trough.
    const series = absoluteDrawdownSeries(curve(100000, 110000, 95000, 118000, 110000));

    expect(drawdowns(series)).toEqual([0, 0, 15000, 0, 8000]);
    expect(series.map((point) => point.peak)).toEqual([100000, 110000, 110000, 118000, 118000]);
  });

  it('holds a flat series at zero, since a repeated peak is still a high-water mark', () => {
    expect(drawdowns(absoluteDrawdownSeries(curve(100000, 100000, 100000)))).toEqual([0, 0, 0]);
  });

  it('derives one point, at zero, from a single point', () => {
    const series = absoluteDrawdownSeries(curve(100000));

    expect(series).toHaveLength(1);
    expect(series[0]).toEqual({
      timestamp: '2024-01-01',
      equity: 100000,
      peak: 100000,
      drawdown: 0,
      readable: true,
    });
  });

  it('never reports a negative drawdown, whatever the equity signs are', () => {
    // The absolute form has no division, so a peak of 0 and a negative equity are
    // arithmetic rather than special cases — this is the boundary the percentage form
    // could not answer.
    const series = absoluteDrawdownSeries(curve(0, -500, -1200, 250));

    expect(drawdowns(series)).toEqual([0, 500, 1200, 0]);
    expect(series.every((point) => point.drawdown >= 0)).toBe(true);
  });

  it('reads the bare-number form, pairing each point with its index', () => {
    const series = absoluteDrawdownSeries([100000, 106000, 99000]);

    expect(drawdowns(series)).toEqual([0, 0, 7000]);
    expect(series.map((point) => point.timestamp)).toEqual([0, 1, 2]);
  });

  it('falls back to the index when a record carries no usable timestamp', () => {
    const series = absoluteDrawdownSeries([
      { equity: 100000 },
      { timestamp: '   ', equity: 99000 },
      { timestamp: 1704067200, equity: 98000 },
    ]);

    expect(series.map((point) => point.timestamp)).toEqual([0, 1, 1704067200]);
  });
});

// ---------------------------------------------------------------------------
// Honest absence
// ---------------------------------------------------------------------------

describe('absoluteDrawdownSeries: an absent curve derives an absent series', () => {
  it('derives an empty series from an empty curve, not a flat line', () => {
    // The defect this guards against: paper mode synthesising an equity curve at
    // 100000.0 when the store is empty. Two flat points here would be the same lie —
    // "this run never lost money" — told from a record that said nothing (Req 14.5).
    expect(absoluteDrawdownSeries([])).toEqual([]);
  });

  it('derives an empty series from anything that is not a curve', () => {
    for (const input of [null, undefined, 0, 'flat', { equity: 100000 }, NaN]) {
      expect(absoluteDrawdownSeries(input)).toEqual([]);
    }
  });
});

describe('absoluteDrawdownSeries: an unreadable point is not a zero drawdown', () => {
  it('carries an absent, null, NaN, infinite or wrongly-typed equity as unreadable', () => {
    const series = absoluteDrawdownSeries([
      { timestamp: 't0', equity: 100000 },
      { timestamp: 't1' },
      { timestamp: 't2', equity: null },
      { timestamp: 't3', equity: Number.NaN },
      { timestamp: 't4', equity: Number.POSITIVE_INFINITY },
      { timestamp: 't5', equity: '98000' },
      { timestamp: 't6', equity: { value: 98000 } },
      'not a point',
      null,
      { timestamp: 't9', equity: 94000 },
    ]);

    // One point out per point in, in order: the gap is visible rather than closed.
    expect(series).toHaveLength(10);
    expect(series.map((point) => point.timestamp)).toEqual([
      't0', 't1', 't2', 't3', 't4', 't5', 't6', 7, 8, 't9',
    ]);

    // Every unreadable point derives null, and not one of them derives 0.
    expect(drawdowns(series)).toEqual([0, null, null, null, null, null, null, null, null, 6000]);
    expect(readability(series)).toEqual([
      true, false, false, false, false, false, false, false, false, true,
    ]);
    expect(series.filter((point) => !point.readable).every((point) => point.equity === null)).toBe(true);
  });

  it('does not advance the running peak past an unreadable point', () => {
    // The 104000 depth at the end is measured from the last equity that could be read.
    // An unreadable value is not evidence of a new high, and the previous equity is not
    // carried forward into it either.
    const series = absoluteDrawdownSeries([
      { timestamp: 't0', equity: 104000 },
      { timestamp: 't1', equity: null },
      { timestamp: 't2', equity: 100000 },
    ]);

    expect(series.map((point) => point.peak)).toEqual([104000, 104000, 104000]);
    expect(drawdowns(series)).toEqual([0, null, 4000]);
  });

  it('reports no peak at all before the first readable point', () => {
    const series = absoluteDrawdownSeries([{ equity: null }, { equity: 100000 }]);

    expect(series[0]).toEqual({
      timestamp: 0,
      equity: null,
      peak: null,
      drawdown: null,
      readable: false,
    });
    expect(series[1].peak).toBe(100000);
  });

  it('derives an all-unreadable series from a curve nothing can be read from', () => {
    const series = absoluteDrawdownSeries([{ equity: null }, 'x', {}]);

    expect(drawdowns(series)).toEqual([null, null, null]);
    expect(countUnreadableDrawdownPoints(series)).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// The incompleteness report
// ---------------------------------------------------------------------------

describe('countUnreadableDrawdownPoints', () => {
  it('counts the gaps in a derived series', () => {
    expect(countUnreadableDrawdownPoints(absoluteDrawdownSeries(curve(1, 2, 3)))).toBe(0);
    expect(
      countUnreadableDrawdownPoints(
        absoluteDrawdownSeries([{ equity: 1 }, { equity: null }, { equity: 3 }, {}]),
      ),
    ).toBe(2);
  });

  it('answers 0 for an empty series and for anything that is not one', () => {
    for (const input of [[], null, undefined, 'series', 42, { readable: false }]) {
      expect(countUnreadableDrawdownPoints(input)).toBe(0);
    }
  });

  it('states the incompleteness in one sentence the page can render', () => {
    // The caveat is worded once here rather than at each call site, so the two tier-2
    // charts cannot disagree about what a gap means.
    expect(REASON_UNREADABLE_EQUITY).toMatch(/gap/);
    expect(REASON_UNREADABLE_EQUITY).toMatch(/measured/);
  });
});

// ---------------------------------------------------------------------------
// Purity
// ---------------------------------------------------------------------------

describe('absoluteDrawdownSeries: purity', () => {
  it('freezes the series and every point in it', () => {
    const series = absoluteDrawdownSeries(curve(100000, 96000));

    expect(Object.isFrozen(series)).toBe(true);
    expect(series.every((point) => Object.isFrozen(point))).toBe(true);
  });

  it('does not mutate the equity series it was given', () => {
    const input = curve(100000, 96000, 101000);
    const before = JSON.stringify(input);

    absoluteDrawdownSeries(input);

    expect(JSON.stringify(input)).toBe(before);
  });

  it('is deterministic — the same curve derives the same series twice', () => {
    const input = curve(100000, 110000, 92500);

    expect(absoluteDrawdownSeries(input)).toEqual(absoluteDrawdownSeries(input));
  });
});
