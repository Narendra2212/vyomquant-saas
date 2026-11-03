/**
 * ═══════════════════════════════════════════════════════════════════════════
 * tests/unit/pages/backtesterCsvExport.test.jsx — BT-04's two CSV builders
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * `tradesCsv` and `equityCurveCsv` (`src/pages/Backtester.jsx`) are pure string builders —
 * the DOM-touching half (`downloadCsv`, a `Blob` + `URL.createObjectURL` + a throwaway
 * `<a>`, the same mechanism `pages/TradeHistory.jsx` and `pages/SecurityLogs.jsx` already
 * use) is not exported and is not under test here, same as neither page tests its own
 * anchor click. What is tested is the shape a trader opens in a spreadsheet: one header
 * row, the declared columns, one data row per trade or per equity point, and the quoting
 * rule that lets a field carrying a comma or a quote still round-trip.
 */

import { describe, expect, it } from 'vitest';

import { equityCurveCsv, tradeDetailRows, tradesCsv } from '../../../src/pages/Backtester';

/** `'a,b\n"c"'` → `['a,b', '"c"']`, naive but exact for CSV built with one `\n` per row. */
const csvLines = (csv) => csv.split('\n');

describe('tradesCsv', () => {
  it('has a header row and one data row per trade, in TRADE_COLUMNS order', () => {
    const trades = [
      {
        trade_id: 1,
        entry_time: '2024-01-01T00:00:00Z',
        entry_price: 100,
        exit_time: '2024-01-02T00:00:00Z',
        exit_price: 110,
        side: 'buy',
        quantity: 2,
        gross_pnl: 20,
        fees: 1,
        net_pnl: 19,
        return_pct: 10,
        duration: 3723, // 1h 02m 03s
      },
    ];

    const csv = tradesCsv(tradeDetailRows(trades));
    const lines = csvLines(csv);

    expect(lines).toHaveLength(2);
    expect(lines[0]).toBe(
      '"#","Entry","Entry price","Exit","Exit price","Side","Quantity","Gross P&L","Fees",'
      + '"Net P&L","Return %","Held for"',
    );
    expect(lines[1]).toBe(
      '"1","2024-01-01T00:00:00Z","100","2024-01-02T00:00:00Z","110","buy","2","20","1","19",'
      + '"10","1h 02m"',
    );
  });

  it('is the empty string — no header, no rows — for no trades', () => {
    expect(tradesCsv([])).toBe('"#","Entry","Entry price","Exit","Exit price","Side",'
      + '"Quantity","Gross P&L","Fees","Net P&L","Return %","Held for"');
  });

  it('is total: garbage input produces the header and nothing else, never a throw', () => {
    expect(() => tradesCsv(null)).not.toThrow();
    expect(() => tradesCsv(undefined)).not.toThrow();
    expect(csvLines(tradesCsv(null))).toHaveLength(1);
  });

  it('doubles an embedded quote and leaves a comma inside the quoted cell', () => {
    const csv = tradesCsv(tradeDetailRows([{ trade_id: 1, side: 'buy, "fast"' }]));
    expect(csv).toContain('"buy, ""fast"""');
  });
});

describe('equityCurveCsv', () => {
  it('has a header row and one data row per equity point, timestamp then equity', () => {
    const results = {
      equity: [
        { timestamp: 1700000000000, equity: 10000 },
        { timestamp: 1700000060000, equity: 10125.5 },
      ],
    };

    const lines = csvLines(equityCurveCsv(results));
    expect(lines).toHaveLength(3);
    expect(lines[0]).toBe('"Timestamp","Equity"');
    expect(lines[1]).toBe('"1700000000000","10000"');
    expect(lines[2]).toBe('"1700000060000","10125.5"');
  });

  it('exports an unreadable point as an empty equity cell, never a fabricated 0', () => {
    const results = { equity: [{ timestamp: 'bad-point', equity: null }] };
    const lines = csvLines(equityCurveCsv(results));
    expect(lines[1]).toBe('"bad-point",""');
  });

  it('is total over a missing or malformed equity series', () => {
    expect(equityCurveCsv(null)).toBe('"Timestamp","Equity"');
    expect(equityCurveCsv({})).toBe('"Timestamp","Equity"');
    expect(equityCurveCsv({ equity: 'not-an-array' })).toBe('"Timestamp","Equity"');
  });
});
