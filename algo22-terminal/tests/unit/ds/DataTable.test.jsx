/**
 * ds/DataTable — vyomquant-ui-redesign task 6.9.
 * Requirements 11.1, 11.3, 11.4, 11.6, 15.4, 17.2, 18.1.
 *
 * WHAT THIS FILE COVERS, AND WHAT IT DELIBERATELY DOES NOT
 * =======================================================
 * The three properties this component is answerable for — column alignment (P19),
 * the pagination partition (P20) and stable, reversible sorting (P29) — are owned by
 * tasks 6.10, 6.11 and 6.13 and are asserted there over generated inputs. This file
 * is the example-based half, and it concentrates on the two behaviours that are easy
 * to get subtly wrong and expensive to get wrong quietly:
 *
 *   * **Sort stability.** `Array.prototype.sort` has been stable since ES2019, so an
 *     implementation that relies on the engine passes a naive test and still leaves
 *     stability undeclared. The cases below pin the two things that make it a
 *     property of the comparator instead: equal keys keep their input order in BOTH
 *     directions, and toggling direction twice returns the identical order.
 *   * **The pagination partition.** Clamping an out-of-range page is the tempting
 *     behaviour and it silently duplicates rows. Walking every page and counting ids
 *     is the only way that shows up. Section 8 adds the other half of the same
 *     question: `pageSlice` staying empty out of range is right, and the *component*
 *     resolving one clamped page number for both the `<tbody>` and the live region is
 *     also right. Only a rendered assertion can catch the two disagreeing.
 *   * **What the row memo does and does not observe.** Section 14 covers `observe` in
 *     both directions, because a mechanism that makes a declared value re-render its
 *     row is worthless if it re-renders the other forty-nine as well.
 *
 * The rendering assertions read `data-align`, `data-column-key`, `data-row-id` and
 * `data-priority` rather than computed styles, because jsdom applies no Tailwind CSS:
 * the question "does every cell in this column carry the column's alignment" is a
 * question about the declaration reaching the cell, and those attributes are where
 * that is observable.
 */

import React from 'react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import DataTable, {
  DEFAULT_PAGE_SIZE,
  NOT_AVAILABLE,
  comparatorFor,
  compareNumeric,
  compareText,
  compareTimestamp,
  formatCellValue,
  nextSort,
  pageCount,
  pageSlice,
  sortRows,
} from '../../../src/components/ds/DataTable';

// ── Fixtures ────────────────────────────────────────────────────────────────────────────

/** The §11.3 worked example, trimmed to what these assertions need. */
const COLUMNS = [
  { key: 'time', header: 'Time', align: 'text', sortable: true, format: 'timestamp', priority: 1 },
  { key: 'market', header: 'Market', align: 'text', sortable: true, format: 'symbol', priority: 1 },
  { key: 'quantity', header: 'Qty', align: 'numeric', sortable: true, format: 'number', priority: 2 },
  { key: 'pnl', header: 'P&L', align: 'numeric', sortable: true, priority: 1 },
  { key: 'strategy', header: 'Strategy', align: 'text', sortable: true, priority: 3 },
];

const ROWS = [
  { id: 'a', time: '2024-03-11T12:04:00Z', market: 'BTC/USDT', quantity: 2, pnl: -120, strategy: 'Mean reversion' },
  { id: 'b', time: '2024-03-11T12:06:00Z', market: 'ETH/USDT', quantity: 10, pnl: 45.5, strategy: 'Momentum' },
  { id: 'c', time: '2024-03-11T12:05:00Z', market: 'SOL/USDT', quantity: 1, pnl: null, strategy: 'Momentum' },
];

/** Normalised the way the component normalises, for the exported sort helpers. */
const sortable = (key, format) => [{ key, format, sortable: true }];

const cellsOf = (columnKey) =>
  Array.from(document.querySelectorAll(`tbody td[data-column-key="${columnKey}"]`));

const bodyRows = () =>
  Array.from(document.querySelectorAll('tbody tr:not([data-row-detail])'));

const rowIds = () => bodyRows().map((row) => row.getAttribute('data-row-id'));

const renderTable = (props) =>
  render(<DataTable caption="Test table" columns={COLUMNS} rows={ROWS} {...props} />);

// ══════════════════════════════════════════════════════════════════════════════════════
// 1. Comparators
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DataTable comparators', () => {
  it('picks the comparator from the format', () => {
    expect(comparatorFor('number')).toBe(compareNumeric);
    expect(comparatorFor('currency')).toBe(compareNumeric);
    expect(comparatorFor('timestamp')).toBe(compareTimestamp);
    expect(comparatorFor('symbol')).toBe(compareText);
    expect(comparatorFor('text')).toBe(compareText);
    // Total over data it has never seen, rather than throwing.
    expect(comparatorFor('percent')).toBe(compareText);
    expect(comparatorFor(undefined)).toBe(compareText);
  });

  it('parses decimal strings, because money arrives as one', () => {
    expect(compareNumeric('9.5', '10')).toBe(-1);
    expect(compareNumeric(2, '2.0')).toBe(0);
  });

  it('sorts anything unreadable below every real value', () => {
    // Not "nulls last": lowest. That is what lets `desc` be the plain negation of
    // `asc` (see the module docblock), and it groups the unknowns either way.
    expect(compareNumeric(null, 0)).toBe(-1);
    expect(compareNumeric(Number.NaN, -1e9)).toBe(-1);
    expect(compareTimestamp('not a date', '1970-01-01T00:00:00Z')).toBe(-1);
    expect(compareText({}, '')).toBe(-1);
    expect(compareNumeric(null, undefined)).toBe(0);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 2. Sorting — stable, and reversible
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DataTable sortRows', () => {
  const columns = sortable('score', 'number');
  const tied = [
    { id: 1, score: 5 },
    { id: 2, score: 5 },
    { id: 3, score: 1 },
    { id: 4, score: 5 },
  ];

  it('keeps equal keys in input order, ascending and descending alike', () => {
    expect(sortRows(tied, columns, { key: 'score', direction: 'asc' }).map((r) => r.id))
      .toEqual([3, 1, 2, 4]);
    expect(sortRows(tied, columns, { key: 'score', direction: 'desc' }).map((r) => r.id))
      .toEqual([1, 2, 4, 3]);
  });

  it('restores the original order when the direction is toggled twice', () => {
    const asc = sortRows(tied, columns, { key: 'score', direction: 'asc' });
    const desc = sortRows(tied, columns, { key: 'score', direction: 'desc' });
    const back = sortRows(tied, columns, { key: 'score', direction: 'asc' });
    expect(back).toEqual(asc);
    expect(desc).not.toEqual(asc);
  });

  it('is a permutation of the same rows, losing and inventing none', () => {
    const sorted = sortRows(ROWS, COLUMNS, { key: 'market', direction: 'desc' });
    expect(sorted).toHaveLength(ROWS.length);
    expect(new Set(sorted).size).toBe(ROWS.length);
    ROWS.forEach((row) => expect(sorted).toContain(row));
    expect(sorted.map((row) => row.id)).toEqual(['c', 'b', 'a']);
  });

  it('leaves the array untouched when there is nothing to sort by', () => {
    expect(sortRows(tied, columns, undefined)).toBe(tied);
    expect(sortRows(tied, columns, { key: 'nope', direction: 'asc' })).toBe(tied);
    // A column that does not declare `sortable` is not sorted: the header shows no
    // `aria-sort`, so ordering by it would make the announced and rendered orders
    // disagree.
    expect(sortRows(tied, [{ key: 'score', format: 'number' }], { key: 'score', direction: 'asc' }))
      .toBe(tied);
  });

  it('toggles asc → desc → asc, with no third state', () => {
    expect(nextSort(undefined, 'time')).toEqual({ key: 'time', direction: 'asc' });
    expect(nextSort({ key: 'time', direction: 'asc' }, 'time')).toEqual({ key: 'time', direction: 'desc' });
    expect(nextSort({ key: 'time', direction: 'desc' }, 'time')).toEqual({ key: 'time', direction: 'asc' });
    // A different column starts over rather than inheriting the direction.
    expect(nextSort({ key: 'time', direction: 'desc' }, 'pnl')).toEqual({ key: 'pnl', direction: 'asc' });
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 3. Pagination — a partition
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DataTable pagination helpers', () => {
  const many = Array.from({ length: 23 }, (_, index) => ({ id: index }));

  it('covers every row exactly once across the pages it reports', () => {
    const pages = pageCount(many.length, 10);
    expect(pages).toBe(3);

    const seen = [];
    for (let page = 1; page <= pages; page += 1) {
      const slice = pageSlice(many, page, 10);
      expect(slice.length).toBeLessThanOrEqual(10);
      slice.forEach((row) => seen.push(row.id));
    }
    expect(seen).toEqual(many.map((row) => row.id));
    expect(new Set(seen).size).toBe(many.length);
  });

  it('returns nothing outside that range instead of clamping', () => {
    // Clamping is what would duplicate the last page's rows for a stale `page`.
    expect(pageSlice(many, 4, 10)).toEqual([]);
    expect(pageSlice(many, 0, 10)).toEqual([]);
    expect(pageSlice(many, -1, 10)).toEqual([]);
  });

  it('treats a non-positive page size as "do not paginate"', () => {
    expect(pageSlice(many, 1, 0)).toBe(many);
    expect(pageCount(many.length, 0)).toBe(1);
  });

  it('never reports fewer than one page', () => {
    expect(pageCount(0, 50)).toBe(1);
    expect(pageCount(Number.NaN, 50)).toBe(1);
  });

  it('defaults to the 50 rows per page design.md §7.7 specifies', () => {
    expect(DEFAULT_PAGE_SIZE).toBe(50);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 4. Display formatting — grouped, never rounded
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DataTable formatCellValue', () => {
  it('has no rendering for a value that is not there', () => {
    // The caller turns `null` into the not-available marker. Never `0`.
    expect(formatCellValue(null, 'number')).toBeNull();
    expect(formatCellValue(undefined, 'currency')).toBeNull();
    expect(formatCellValue('', 'text')).toBeNull();
    expect(formatCellValue(Number.NaN, 'number')).toBeNull();
    expect(formatCellValue('later', 'timestamp')).toBeNull();
    expect(formatCellValue({}, 'text')).toBeNull();
  });

  it('groups thousands without rounding, and keeps a server string exact', () => {
    expect(formatCellValue(1234567.5, 'number')).toBe('1,234,567.5');
    expect(formatCellValue(-1234.25, 'currency')).toBe('-1,234.25');
    // The trailing zero survives: it is what the server said the price was.
    expect(formatCellValue('1.50', 'currency')).toBe('1.50');
    expect(formatCellValue('0.000012345', 'number')).toBe('0.000012345');
    expect(formatCellValue(999, 'number')).toBe('999');
  });

  it('leaves the numbers in a text column ungrouped, because 2024 is not 2,024', () => {
    expect(formatCellValue(2024, 'text')).toBe('2024');
    expect(formatCellValue('BTC/USDT', 'symbol')).toBe('BTC/USDT');
  });

  it('renders a timestamp in UTC, to the second', () => {
    expect(formatCellValue('2024-03-11T12:04:00Z', 'timestamp')).toBe('2024-03-11 12:04:00Z');
    expect(formatCellValue(new Date(0), 'timestamp')).toBe('1970-01-01 00:00:00Z');
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 5. The contract it will not render without
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DataTable required caption', () => {
  beforeEach(() => {
    // React logs a rendering failure through console.error. The failure is the
    // assertion here, so the noise is suppressed rather than read.
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('refuses to render without one, because it is the accessible name', () => {
    expect(() => render(<DataTable columns={COLUMNS} rows={ROWS} />)).toThrow(/caption/i);
  });

  it('uses it as the accessible name rather than as a visible title', () => {
    renderTable();
    const table = screen.getByRole('table', { name: 'Test table' });
    expect(table.querySelector('caption').textContent).toBe('Test table');
    expect(table.querySelector('caption').className).toContain('sr-only');
  });

  it('rejects a density it does not have', () => {
    expect(() => renderTable({ density: 'cosy' })).toThrow(/density/i);
  });

  it('rejects duplicate column keys', () => {
    expect(() =>
      render(
        <DataTable
          caption="x"
          rows={ROWS}
          columns={[{ key: 'pnl', header: 'A' }, { key: 'pnl', header: 'B' }]}
        />,
      ),
    ).toThrow(/unique/i);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 6. Alignment is a column property (Requirement 11.3)
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DataTable column alignment', () => {
  it('gives every cell in a numeric column the numeric treatment, and no others', () => {
    renderTable();

    ['quantity', 'pnl'].forEach((key) => {
      const cells = cellsOf(key);
      expect(cells).toHaveLength(ROWS.length);
      cells.forEach((cell) => {
        expect(cell.getAttribute('data-align')).toBe('numeric');
        expect(cell.className).toContain('text-right');
        expect(cell.className).toContain('tabular-nums');
        expect(cell.className).toContain('font-mono');
      });
    });

    ['time', 'market', 'strategy'].forEach((key) => {
      cellsOf(key).forEach((cell) => {
        expect(cell.getAttribute('data-align')).toBe('text');
        expect(cell.className).not.toContain('text-right');
        expect(cell.className).not.toContain('tabular-nums');
      });
    });
  });

  it('aligns the header with the column it heads', () => {
    renderTable();
    const numericHead = document.querySelector('th[data-column-key="quantity"]');
    expect(numericHead.getAttribute('data-align')).toBe('numeric');
    expect(numericHead.className).toContain('text-right');
  });

  it('sorts a numeric column numerically even when it declares no format', () => {
    // `pnl` in design.md §11.3's example: align numeric, a `render`, no `format`. As
    // text, -120 would sort between -12 and -13.
    renderTable({ sort: { key: 'pnl', direction: 'asc' }, onSortChange: () => {} });
    expect(rowIds()).toEqual(['c', 'a', 'b']);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 7. Sortable headers (Requirement 15.4)
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DataTable sortable headers', () => {
  it('announces the sort state on the column that carries it', () => {
    renderTable({ sort: { key: 'market', direction: 'desc' }, onSortChange: () => {} });

    expect(document.querySelector('th[data-column-key="market"]').getAttribute('aria-sort'))
      .toBe('descending');
    expect(document.querySelector('th[data-column-key="time"]').getAttribute('aria-sort'))
      .toBe('none');
  });

  it('makes a sortable header a real button and reports the next sort', () => {
    const onSortChange = vi.fn();
    renderTable({ sort: { key: 'market', direction: 'asc' }, onSortChange });

    fireEvent.click(screen.getByRole('button', { name: /market/i }));
    expect(onSortChange).toHaveBeenCalledWith({ key: 'market', direction: 'desc' });
  });

  it('renders text, not a dead button, when there is nothing to report a sort to', () => {
    // Requirement 19.4: a control that cannot do anything must not be rendered.
    renderTable();
    expect(screen.queryByRole('button', { name: /market/i })).toBeNull();
    expect(document.querySelector('th[data-column-key="market"]').getAttribute('aria-sort'))
      .toBe('none');
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 8. Pagination in the rendered table (Requirement 11.4)
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DataTable pagination rendering', () => {
  const many = Array.from({ length: 7 }, (_, index) => ({
    id: `r${index}`,
    time: null,
    market: `M${index}`,
    quantity: index,
    pnl: index,
    strategy: 's',
  }));

  /** The live region's promise, read back out as `[first, last, total]`. */
  const announcedRange = () => {
    const match = /Showing (\d+)–(\d+) of (\d+)/.exec(screen.getByRole('status').textContent);
    return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
  };

  it('renders one page at a time and walks the whole set without repeating a row', () => {
    const seen = [];
    for (let page = 1; page <= 3; page += 1) {
      const view = render(
        <DataTable caption="Paged" columns={COLUMNS} rows={many} page={page} pageSize={3} onPageChange={() => {}} />,
      );
      const ids = rowIds();
      expect(ids.length).toBeLessThanOrEqual(3);
      ids.forEach((id) => seen.push(id));
      view.unmount();
    }
    expect(seen).toEqual(many.map((row) => row.id));
  });

  it('states the range it is showing in a live region', () => {
    render(
      <DataTable caption="Paged" columns={COLUMNS} rows={many} page={2} pageSize={3} onPageChange={() => {}} />,
    );
    expect(screen.getByRole('status').textContent).toBe('Showing 4–6 of 7');
  });

  it('moves by page, 1-based, and stops at both ends', () => {
    const onPageChange = vi.fn();
    const { unmount } = render(
      <DataTable caption="Paged" columns={COLUMNS} rows={many} page={1} pageSize={3} onPageChange={onPageChange} />,
    );
    expect(screen.getByRole('button', { name: /previous page/i }).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /next page/i }));
    expect(onPageChange).toHaveBeenCalledWith(2);
    unmount();

    render(
      <DataTable caption="Paged" columns={COLUMNS} rows={many} page={3} pageSize={3} onPageChange={onPageChange} />,
    );
    expect(screen.getByRole('button', { name: /next page/i }).disabled).toBe(true);
  });

  it('renders no page controls when everything fits', () => {
    renderTable();
    expect(screen.queryByRole('navigation')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('shows the last real page for a stale `page`, instead of announcing rows it never rendered', () => {
    // The case a filter creates: the set narrows to 7 rows while the page state still
    // says 9. The footer has always clamped, so it announced "Showing 7–7 of 7"; the
    // body sliced with the raw prop, so `pageSlice` correctly returned nothing and the
    // `<tbody>` was absent altogether. A live region describing rows that are not on
    // screen is worse than either behaviour on its own.
    render(
      <DataTable caption="Paged" columns={COLUMNS} rows={many} page={9} pageSize={3} onPageChange={() => {}} />,
    );

    expect(rowIds()).toEqual(['r6']);
    expect(announcedRange()).toEqual([7, 7, 7]);
    expect(screen.getByText('Page 3 of 3')).toBeTruthy();
  });

  it('shows the first page for a `page` below the range', () => {
    render(
      <DataTable caption="Paged" columns={COLUMNS} rows={many} page={0} pageSize={3} onPageChange={() => {}} />,
    );

    expect(rowIds()).toEqual(['r0', 'r1', 'r2']);
    expect(announcedRange()).toEqual([1, 3, 7]);
  });

  it('renders exactly the rows the live region says it is showing, at every page number', () => {
    // Walked past both ends, because in range the body and the footer agree either
    // way — the disagreement only exists where `pageSlice` is deliberately empty
    // (Property 20). So this is a statement about the *component* resolving one page
    // number for both halves, not about that function, which is unchanged.
    const pages = pageCount(many.length, 3);

    for (let page = -1; page <= pages + 3; page += 1) {
      const view = render(
        <DataTable caption="Paged" columns={COLUMNS} rows={many} page={page} pageSize={3} onPageChange={() => {}} />,
      );

      const [first, last, total] = announcedRange();
      expect(total).toBe(many.length);
      expect(rowIds()).toEqual(many.slice(first - 1, last).map((row) => row.id));

      view.unmount();
    }
  });

  it('leaves rows alone when totalCount says the server already sliced them', () => {
    render(
      <DataTable
        caption="Server paged"
        columns={COLUMNS}
        rows={many.slice(3, 6)}
        page={2}
        pageSize={3}
        totalCount={7}
        onPageChange={() => {}}
      />,
    );
    // All three given rows render — not page 2 of the three on hand, which would be
    // empty.
    expect(rowIds()).toEqual(['r3', 'r4', 'r5']);
    expect(screen.getByRole('status').textContent).toBe('Showing 4–6 of 7');
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 9. Empty, and not-available
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DataTable empty and missing values', () => {
  it('renders no rows region at all, and no empty state of its own', () => {
    renderTable({ rows: [] });
    expect(document.querySelector('tbody')).toBeNull();
    // The header survives, so the columns are still legible. The `Panel` renders
    // `EmptyState` — this component must not, or Requirement 11.5's two cases
    // collapse into one message again.
    expect(document.querySelectorAll('th[data-column-key]').length).toBe(COLUMNS.length);
    expect(document.body.textContent).not.toMatch(/no trades|no data|no rows/i);
  });

  it('marks a missing value not available, in words as well as in a glyph', () => {
    renderTable();
    const pnlCells = cellsOf('pnl');
    expect(pnlCells[2].textContent).toContain(NOT_AVAILABLE);
    expect(pnlCells[2].textContent).toContain('Not available');
    // Never 0.
    expect(pnlCells[2].textContent).not.toMatch(/\b0\b/);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 10. Keyboard (Requirement 18.1)
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DataTable keyboard access', () => {
  it('makes a clickable row focusable and activates on Enter and on Space', () => {
    const onRowClick = vi.fn();
    renderTable({ onRowClick });

    const [first] = bodyRows();
    expect(first.getAttribute('tabindex')).toBe('0');

    fireEvent.keyDown(first, { key: 'Enter' });
    fireEvent.keyDown(first, { key: ' ' });
    expect(onRowClick).toHaveBeenCalledTimes(2);
    expect(onRowClick.mock.calls[0][0]).toBe(ROWS[0]);

    // A key that is not an activation does nothing.
    fireEvent.keyDown(first, { key: 'a' });
    expect(onRowClick).toHaveBeenCalledTimes(2);
  });

  it('reaches a row through a link in its first cell when rowHref is given', () => {
    renderTable({ rowHref: (row) => `/app/trades/${row.id}` });

    const link = screen.getByRole('link', { name: /2024-03-11 12:04:00Z/ });
    expect(link.getAttribute('href')).toBe('/app/trades/a');
    // The link is the activation surface, so the row itself is not a second one.
    expect(bodyRows()[0].getAttribute('tabindex')).toBeNull();
  });

  it('leaves a row that does nothing out of the tab order', () => {
    renderTable();
    bodyRows().forEach((row) => expect(row.getAttribute('tabindex')).toBeNull());
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 11. Below the laptop breakpoint (Requirement 17.2)
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DataTable responsive reduction', () => {
  it('scrolls rather than clips', () => {
    const { container } = renderTable();
    // The scroll container is the table's own parent, not the outer `w-full` block —
    // that one also holds the pagination footer, which must not scroll with the rows.
    // What is observable here is the *declaration*: jsdom performs no layout, so
    // `scrollWidth`/`clientWidth` are both 0 and nothing here can witness a real
    // overflow. Task 8.11's Playwright responsive pass is what asserts the rendered
    // table is never clipped at a supported width (Requirement 17.2).
    const wrapper = container.querySelector('table').parentElement;
    expect(wrapper.className).toContain('overflow-x-auto');
    expect(wrapper.style.scrollbarGutter).toBe('stable');
  });

  it('hides only the priority-3 columns below the breakpoint', () => {
    renderTable();
    const dropped = document.querySelector('th[data-column-key="strategy"]');
    expect(dropped.getAttribute('data-priority')).toBe('3');
    expect(dropped.className).toContain('max-laptop:hidden');

    ['time', 'market', 'quantity', 'pnl'].forEach((key) => {
      expect(document.querySelector(`th[data-column-key="${key}"]`).className)
        .not.toContain('max-laptop:hidden');
    });
    cellsOf('strategy').forEach((cell) => expect(cell.className).toContain('max-laptop:hidden'));
  });

  it('surfaces the hidden columns in a per-row expand that exists only below it', () => {
    renderTable();

    // The expander is named for the row's first *shown* column, which is `time` here
    // and not `market`: the label has to come from a column that is still on screen
    // below the breakpoint, and it is the row's identity in that reduced view. Three
    // rows therefore give three distinctly named expanders rather than three "Show
    // remaining columns" buttons a screen reader cannot tell apart.
    const rowLabel = cellsOf('time')[0].textContent;
    expect(rowLabel).toBe('2024-03-11 12:04:00Z');
    const expander = screen.getByRole('button', {
      name: `Show remaining columns for ${rowLabel}`,
    });
    expect(expander.closest('td').className).toContain('laptop:hidden');
    expect(expander.getAttribute('aria-expanded')).toBe('false');

    fireEvent.click(expander);
    expect(expander.getAttribute('aria-expanded')).toBe('true');

    const detail = document.querySelector('tr[data-row-detail]');
    expect(detail.className).toContain('laptop:hidden');
    expect(detail.textContent).toContain('Mean reversion');
    // Everything still shown at that width, plus the expander itself.
    expect(detail.querySelector('td').getAttribute('colspan')).toBe('5');

    fireEvent.click(expander);
    expect(document.querySelector('tr[data-row-detail]')).toBeNull();
  });

  it('adds no expander when no column is droppable', () => {
    renderTable({ columns: COLUMNS.filter((column) => column.priority !== 3) });
    expect(screen.queryByRole('button', { name: /remaining columns/i })).toBeNull();
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 12. Sticky header (Requirement 15.4)
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DataTable sticky header', () => {
  it('bounds the scroll container, or the header has nothing to stick to', () => {
    const { container } = renderTable({ stickyHeader: true });
    // Same wrapper as above: the table's parent, which is the element `top: 0` resolves
    // against. Whether the header *visibly* stays put is layout, and jsdom has none —
    // `offsetHeight` is 0 and nothing scrolls — so what is asserted is the pair of
    // declarations without which sticky is inert: a bounded height and a scroll axis.
    // Task 8.11's Playwright pass watches the rendered header.
    const wrapper = container.querySelector('table').parentElement;
    expect(wrapper.style.maxHeight).not.toBe('');
    expect(wrapper.style.overflowY).toBe('auto');

    const head = document.querySelector('th[data-column-key="time"]');
    expect(head.className).toContain('sticky');
    expect(head.className).toContain('bg-surface-panel');
    expect(head.style.zIndex).toBe('var(--z-sticky)');
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 13. One tick re-renders one row (design.md §13.2c)
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DataTable row memoisation', () => {
  const rendered = [];

  /** Records which rows actually re-rendered. Called only when its `<tr>` renders. */
  function Recorder({ value, row }) {
    rendered.push(row.id);
    return <span>{String(value)}</span>;
  }

  const columns = [
    { key: 'market', header: 'Market', align: 'text' },
    { key: 'pnl', header: 'P&L', align: 'numeric', render: Recorder },
  ];

  const build = (pnls) =>
    pnls.map((pnl, index) => ({ id: `r${index}`, market: `M${index}`, pnl }));

  beforeEach(() => {
    rendered.length = 0;
  });

  it('re-renders only the row whose projected value changed', () => {
    const { rerender } = render(
      <DataTable caption="Live" columns={columns} rows={build([1, 2, 3])} getRowId={(row) => row.id} />,
    );
    expect(rendered).toEqual(['r0', 'r1', 'r2']);
    rendered.length = 0;

    // A tick: every row object is new, one value differs.
    rerender(
      <DataTable caption="Live" columns={columns} rows={build([1, 99, 3])} getRowId={(row) => row.id} />,
    );
    expect(rendered).toEqual(['r1']);
  });

  it('re-renders nothing when new row objects carry the same values', () => {
    const { rerender } = render(
      <DataTable caption="Live" columns={columns} rows={build([1, 2, 3])} getRowId={(row) => row.id} />,
    );
    rendered.length = 0;

    rerender(
      <DataTable caption="Live" columns={columns} rows={build([1, 2, 3])} getRowId={(row) => row.id} />,
    );
    expect(rendered).toEqual([]);
  });

  it('still hands the activation handler the current row after a tick', () => {
    const onRowClick = vi.fn();
    const first = build([1, 2, 3]);
    const { rerender } = render(
      <DataTable caption="Live" columns={columns} rows={first} getRowId={(row) => row.id} onRowClick={onRowClick} />,
    );

    const second = build([1, 2, 3]);
    rerender(
      <DataTable caption="Live" columns={columns} rows={second} getRowId={(row) => row.id} onRowClick={onRowClick} />,
    );

    fireEvent.click(bodyRows()[0]);
    // The memo skipped the row, so the handler must not have closed over the row it
    // was mounted with.
    expect(onRowClick.mock.calls[0][0]).toBe(second[0]);
    expect(onRowClick.mock.calls[0][0]).not.toBe(first[0]);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 14. A cell declaring what it reads beyond the projection (design.md §13.2c)
// ══════════════════════════════════════════════════════════════════════════════════════

/**
 * The two directions `observe` has to hold in, and why both are needed.
 *
 * An action cell's visible state — disabled while its request is in flight — is page
 * state, not a row field, so no projected value moves when it changes. The memo above
 * therefore skips the row and the button stays enabled: correct by its own contract and
 * wrong on screen. `observe` closes that, and the interesting risk is that it closes it
 * by re-rendering everything. So the second test is the one that protects §13.2c's "one
 * tick re-renders one `<tr>`": a rebuilt `observe` closure and fifty new row objects
 * must still re-render nothing when no value moved.
 */
describe('DataTable observed dependencies', () => {
  const rendered = [];

  /**
   * An action cell reading page state through `observed` rather than a closure.
   *
   * Declared here, at module scope for the table, precisely because it must not be the
   * thing that changes: a cell that closed over `closingId` would have to be rebuilt
   * with `columns`, and `columns` identity is in the memo compare.
   */
  function ActionCell({ row, observed }) {
    rendered.push(row.id);
    return (
      <button type="button" disabled={observed.closing === true}>
        {observed.closing === true ? `Closing ${row.id}` : `Close ${row.id}`}
      </button>
    );
  }

  const columns = [
    { key: 'market', header: 'Market', align: 'text' },
    { key: 'pnl', header: 'P&L', align: 'numeric' },
    { key: 'action', header: 'Action', align: 'text', render: ActionCell },
  ];

  const build = (pnls) => pnls.map((pnl, index) => ({ id: `r${index}`, market: `M${index}`, pnl }));

  /** `observe` is inline on purpose: a new closure every render is the normal case. */
  const table = (closingId, pnls) => (
    <DataTable
      caption="Live"
      columns={columns}
      rows={build(pnls)}
      getRowId={(row) => row.id}
      observe={(row) => ({ closing: closingId === row.id })}
    />
  );

  beforeEach(() => {
    rendered.length = 0;
  });

  it('re-renders the one row whose declared value moved, and repaints its cell', () => {
    const { rerender } = render(table(null, [1, 2, 3]));
    expect(rendered).toEqual(['r0', 'r1', 'r2']);
    rendered.length = 0;

    // No cell value changes. Only page state does — the thing the projection cannot see.
    rerender(table('r1', [1, 2, 3]));

    expect(rendered).toEqual(['r1']);
    expect(screen.getByRole('button', { name: 'Closing r1' }).disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Close r0' }).disabled).toBe(false);
  });

  it('re-renders nothing on a tick that moves neither a cell value nor a declared one', () => {
    // This is the §13.2c guarantee. Every row object is new and the `observe` closure is
    // new, so a compare that looked at either identity would re-render all three.
    const { rerender } = render(table('r1', [1, 2, 3]));
    rendered.length = 0;

    rerender(table('r1', [1, 2, 3]));

    expect(rendered).toEqual([]);
    expect(screen.getByRole('button', { name: 'Closing r1' }).disabled).toBe(true);
  });

  it('leaves the rows whose declared values held still out of an unrelated tick', () => {
    const { rerender } = render(table('r1', [1, 2, 3]));
    rendered.length = 0;

    // A `pnl` tick on r2 while the action state stays put.
    rerender(table('r1', [1, 2, 99]));

    expect(rendered).toEqual(['r2']);
  });

  it('hands a cell an empty record when the table declares no observe', () => {
    // Backwards compatibility is the requirement, so `observed.closing` has to read as
    // `undefined` rather than throw on every table written before this existed.
    render(
      <DataTable caption="Live" columns={columns} rows={build([1, 2])} getRowId={(row) => row.id} />,
    );

    expect(rendered).toEqual(['r0', 'r1']);
    expect(screen.getByRole('button', { name: 'Close r0' }).disabled).toBe(false);
  });

  it('refuses a declaration the memo cannot compare by name', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    // An array or a scalar would leave the memo observing nothing while the call site
    // still looked correct — the failure `observe` exists to remove.
    expect(() =>
      render(
        <DataTable
          caption="Live"
          columns={columns}
          rows={build([1])}
          getRowId={(row) => row.id}
          observe={(row) => [row.id]}
        />,
      ),
    ).toThrow(/observe/i);
    vi.restoreAllMocks();
  });
});
