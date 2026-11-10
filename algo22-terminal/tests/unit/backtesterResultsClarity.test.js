/*
 * BT-05 / BT-06 / UX — what the results page tells a trader about the run.
 *
 *  - a run that simulated less than the requested window says so, in words;
 *  - the equity and drawdown curves are drawn against dates when the engine reported dates;
 *  - the trade chart splits wins from losses without inventing a value for an unreadable trade.
 */
import { describe, it, expect } from 'vitest';
import { coverageNotice, resultCurves, tradePnlRows } from '../../src/pages/Backtester';

describe('coverageNotice', () => {
  const base = {
    requested_start: '2022-10-18T00:00:00Z',
    requested_end: '2026-11-10T00:00:00Z',
    first_bar: '2023-12-28T08:00:00Z',
    last_bar: '2024-04-10T11:45:00Z',
    bars: 10000,
    bar_cap: 10000,
  };

  it('says nothing when the run covered what was asked', () => {
    expect(coverageNotice({ ...base, truncated_by_cap: false, starts_after_request: false })).toBeNull();
  });

  it('names the covered dates when the bar cap cut the run short', () => {
    const notice = coverageNotice({ ...base, truncated_by_cap: true, starts_after_request: false });
    expect(notice.title).toContain('2023-12-28 → 2024-04-10');
    expect(notice.body).toMatch(/10,000 bars/);
  });

  it('says so when the exchange has no earlier data', () => {
    const notice = coverageNotice({ ...base, truncated_by_cap: false, starts_after_request: true });
    expect(notice.body).toMatch(/no data before 2023-12-28/);
  });

  it('is total over an absent or malformed answer', () => {
    expect(coverageNotice(null)).toBeNull();
    expect(coverageNotice({})).toBeNull();
    expect(coverageNotice('x')).toBeNull();
  });
});

describe('resultCurves axis', () => {
  it('uses dates when every point carries an ISO timestamp', () => {
    const curves = resultCurves({
      equity: [
        { timestamp: '2024-01-01T00:00:00Z', equity: 100 },
        { timestamp: '2024-01-01T00:15:00Z', equity: 101 },
      ],
    });
    expect(curves.dated).toBe(true);
    expect(curves.rows[1].date).toBe('2024-01-01T00:15:00Z');
  });

  it('keeps the position axis when any stamp is not a date', () => {
    const curves = resultCurves({
      equity: [
        { timestamp: 1700000000000000000, equity: 100 },
        { timestamp: '2024-01-01T00:15:00Z', equity: 101 },
      ],
    });
    expect(curves.dated).toBe(false);
    expect(curves.rows[0]).not.toHaveProperty('date');
  });
});

describe('tradePnlRows', () => {
  it('puts each net P&L under win or loss by its sign', () => {
    expect(tradePnlRows([{ net_pnl: 5 }, { net_pnl: -2 }, { net_pnl: 0 }])).toEqual([
      { trade: 1, win: 5, loss: null },
      { trade: 2, win: null, loss: -2 },
      { trade: 3, win: 0, loss: null },
    ]);
  });

  it('leaves out a trade whose P&L cannot be read rather than drawing a zero', () => {
    expect(tradePnlRows([{ net_pnl: 'x' }, { net_pnl: 3 }, {}])).toEqual([{ trade: 2, win: 3, loss: null }]);
    expect(tradePnlRows(null)).toEqual([]);
  });
});
