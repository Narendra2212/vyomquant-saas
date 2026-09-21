/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Feature: vyomquant-ui-redesign — Property 19
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Task 6.10. `design.md` §11.3, §19.
 *
 *   * **Property 19: Column alignment is determined solely by the column declaration.**
 *     **Validates: Requirements 11.3, 15.4**
 *
 * *For any* row set and *for any* column configuration, every rendered cell in a column
 * declared numeric carries the numeric alignment and tabular numerals, and no cell in a
 * column not so declared carries them.
 *
 * WHY THIS IS A RENDERED PROPERTY AND NOT A CALL ON A HELPER
 * --------------------------------------------------------
 * `alignmentClasses()` is a private four-line function in `ds/DataTable.jsx` and asserting
 * on it would assert nothing: Requirement 11.3 was false before this component existed not
 * because anybody computed the wrong class string but because five call sites each decided
 * alignment per cell, and `TradeHistory` left every numeric column ranged left. The claim
 * worth checking is therefore that the DECLARATION REACHES THE CELL — every cell, in every
 * column, over row values chosen to tempt a per-cell decision. That is only observable in a
 * rendered table, so every clause below reads the DOM.
 *
 * WHAT THIS ADDS OVER `tests/unit/ds/DataTable.test.jsx`
 * ----------------------------------------------------
 * That suite already pins the §11.3 worked example by name: `quantity` and `pnl` carry the
 * numeric treatment, `time` / `market` / `strategy` do not, and the header agrees with its
 * column. Restating that here over generated input would be the same assertion with more
 * machinery. What is generated instead is the part an example cannot reach — the word
 * "solely":
 *
 *   * **Not of the cell's value.** A text column holding `'1200.50'` and a numeric column
 *     holding `'BTC/USDT'` are both generated, so a component that looked at the value would
 *     be caught rather than flattered by a fixture whose numeric columns happen to hold
 *     numbers.
 *   * **Not of the row.** Two independently generated row sets are rendered through the SAME
 *     column list and the two alignment maps are compared to each other, not only to the
 *     expectation. A per-row decision shows up as a disagreement between the two.
 *   * **Not of its position.** The same declarations are re-rendered rotated, so a column
 *     that was third is now first. Alignment keyed by column, not by index.
 *   * **Not of where in the DOM the cell landed.** Below `--breakpoint-laptop` a
 *     `priority: 3` column renders a SECOND time, as a `<dd>` in the per-row expand. That is
 *     the one place in the component where alignment is decided twice, and the only place a
 *     copy of the rule could drift from the original.
 *
 * `align` is also generated OUTSIDE its vocabulary — `'right'`, `'NUMERIC'`, `null` — because
 * a column declaration is data and data can be wrong. `normalizeColumns` accepts only the
 * exact string `'numeric'`, so anything else is a text column; that is the expectation here,
 * not a tolerated ambiguity.
 *
 * HOW ALIGNMENT IS READ
 * --------------------
 * `data-align` plus the three Tailwind utilities, never a computed style: jsdom applies no
 * stylesheet, so `getComputedStyle(cell).textAlign` is `''` for every cell in this file and a
 * property asserting on it would pass over a component that emitted no classes at all. The
 * attribute is the declaration having arrived and the utilities are the treatment it asks
 * for; both are checked, so dropping either one fails.
 */

import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';
import fc from 'fast-check';

import DataTable, {
  COLUMN_ALIGNMENTS,
  COLUMN_FORMATS,
  DROPPED_PRIORITY,
} from '../../../src/components/ds/DataTable';

/**
 * design.md §19: "minimum 100 iterations per property".
 *
 * The unit of work here is a rendered table — two or three of them per run — rather than a
 * pure call, which is why this is the floor rather than the 300 the module-level properties
 * in `tests/unit/lib` use. The precedent is
 * `tests/unit/design/tierDocumentOrder.property.test.jsx`, which records the same decision
 * for a property whose unit of work is a whole page. Nothing is softened to buy it: the
 * witnesses at the end of each clause demand that the interesting shapes were actually drawn.
 */
const RUNS = { numRuns: 100 };

/* ══════════════════════════════════════════════════════════════════════════
 * THE TREATMENT, AS TWO RECORDS
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * What a cell reports about its alignment.
 *
 * Compared as a whole record with `toEqual` so that a cell which kept `data-align="numeric"`
 * while losing `tabular-nums` fails on the difference instead of on whichever line happened
 * to be checked first.
 */
const treatmentOf = (cell) => ({
  align: cell.getAttribute('data-align'),
  right: cell.className.includes('text-right'),
  left: cell.className.includes('text-left'),
  mono: cell.className.includes('font-mono'),
  tabular: cell.className.includes('tabular-nums'),
});

/** Right-ranged, monospaced, tabular figures. Requirement 11.3's "align consistently". */
const NUMERIC_TREATMENT = Object.freeze({
  align: 'numeric',
  right: true,
  left: false,
  mono: true,
  tabular: true,
});

/** Everything else. Explicitly `text-left`, and explicitly none of the numeric three. */
const TEXT_TREATMENT = Object.freeze({
  align: 'text',
  right: false,
  left: true,
  mono: false,
  tabular: false,
});

/** The treatment a declaration asks for. The whole of the property's right-hand side. */
const treatmentFor = (declaredAlign) =>
  (declaredAlign === 'numeric' ? NUMERIC_TREATMENT : TEXT_TREATMENT);

/* ══════════════════════════════════════════════════════════════════════════
 * GENERATORS
 * ══════════════════════════════════════════════════════════════════════════ */

/** Column keys drawn from a real table's vocabulary, so a counterexample reads like a table. */
const COLUMN_KEYS = [
  'time',
  'market',
  'quantity',
  'pnl',
  'strategy',
  'status',
  'fee',
  'notional',
];

/**
 * What `align` may say.
 *
 * The two declared values, then four things a wrong declaration looks like. `'right'` is the
 * CSS value somebody will reach for, `'NUMERIC'` is the same word mis-cased, and `null` /
 * `undefined` are an absent declaration. All four must render as text, because
 * `normalizeColumns` promotes nothing but the exact string.
 */
const alignArb = fc.oneof(
  { weight: 5, arbitrary: fc.constantFrom(...COLUMN_ALIGNMENTS) },
  { weight: 1, arbitrary: fc.constantFrom('right', 'NUMERIC', null, undefined) },
);

/** `format` picks the comparator and the default rendering — never the alignment. */
const formatArb = fc.oneof(
  { weight: 5, arbitrary: fc.constantFrom(...COLUMN_FORMATS) },
  { weight: 1, arbitrary: fc.constantFrom(undefined, 'money', 'int') },
);

/**
 * A `render` component, attached to some columns.
 *
 * Module scope and value-blind: a column's own renderer is the most plausible place for a
 * per-cell alignment decision to be smuggled in, and a cell whose content came from here must
 * still sit in its column's alignment. Declared as a constant rather than generated so a
 * shrunk counterexample prints `render: true` instead of a function body.
 */
const Marker = ({ value }) => <span data-rendered="true">{String(value)}</span>;

/** An element already in the row, which the component renders as given. */
const ELEMENT_VALUE = <em data-element-value="true">given</em>;

/**
 * One cell value.
 *
 * Deliberately crossed against the columns: a numeric column will be handed `'BTC/USDT'` and
 * a text column `'1200.50'`, because "alignment is not a function of the value" is only
 * tested where the value disagrees with the column. The absent / `null` / empty-string draws
 * reach the not-available marker, which is a cell with no text and still has an alignment.
 */
const valueArb = fc.oneof(
  { weight: 4, arbitrary: fc.oneof(fc.integer({ min: -9999, max: 9999 }), fc.double({ min: -1e6, max: 1e6, noNaN: true })) },
  { weight: 3, arbitrary: fc.constantFrom('1200.50', '-0.004', '0', '1,000.10', '2024') },
  { weight: 3, arbitrary: fc.constantFrom('BTC/USDT', 'Mean reversion', 'running', '', '   ') },
  { weight: 2, arbitrary: fc.constantFrom(null, undefined, true, false, 0, -0) },
  { weight: 1, arbitrary: fc.constantFrom({}, [], Number.NaN, '2024-03-11T12:04:00Z') },
  { weight: 1, arbitrary: fc.constant(ELEMENT_VALUE) },
);

/** A column declaration: valid where the component demands it, arbitrary everywhere else. */
const columnArb = (key) =>
  fc.record({
    align: alignArb,
    format: formatArb,
    sortable: fc.boolean(),
    // Around and across `DROPPED_PRIORITY`, so some runs render the per-row expand.
    priority: fc.integer({ min: 1, max: DROPPED_PRIORITY + 1 }),
    rendered: fc.boolean(),
  }).map(({ align, format, sortable, priority, rendered }) => ({
    key,
    // Non-empty, because the component will not render a column without one — and the
    // header is not what this property is about.
    header: key.toUpperCase(),
    align,
    format,
    sortable,
    priority,
    ...(rendered ? { render: Marker } : null),
  }));

/** A column configuration: one to five columns with distinct keys. */
const columnsArb = fc
  .uniqueArray(fc.constantFrom(...COLUMN_KEYS), { minLength: 1, maxLength: 5 })
  .chain((keys) => fc.tuple(...keys.map((key) => columnArb(key))));

/**
 * A row set over the given keys.
 *
 * `requiredKeys: []` so a row may simply omit a column's field, which is the ordinary case
 * for a table over a partial server body and renders the not-available marker. Ids are added
 * here rather than generated: they are the memo key and the `data-row-id` these sweeps read,
 * so they must be distinct, and nothing about the property depends on their shape.
 */
const rowsArb = (keys) =>
  fc
    .array(fc.record(Object.fromEntries(keys.map((key) => [key, valueArb])), { requiredKeys: [] }), {
      minLength: 0,
      maxLength: 6,
    })
    .map((rows) => rows.map((row, index) => ({ id: `r${index}`, ...row })));

/** A configuration and two independent row sets over it. */
const tableArb = columnsArb.chain((columns) =>
  fc.tuple(
    fc.constant(columns),
    rowsArb(columns.map((column) => column.key)),
    rowsArb(columns.map((column) => column.key)),
  ),
);

/* ══════════════════════════════════════════════════════════════════════════
 * READING THE TABLE
 * ══════════════════════════════════════════════════════════════════════════ */

const mount = (columns, rows) =>
  render(
    <DataTable
      caption="Property 19 table"
      columns={columns}
      rows={rows}
      // Larger than any generated row set, so pagination never hides a cell this
      // property is about. Pagination is Property 20's subject (task 6.11).
      pageSize={50}
    />,
  );

/** Every body cell in a column, in document order. */
const cellsOf = (key) =>
  [...document.querySelectorAll(`tbody td[data-column-key="${key}"]`)];

/** Every rendered treatment in a column, as records. The property's left-hand side. */
const treatmentsOf = (key) => cellsOf(key).map(treatmentOf);

/** The alignment map of the whole table: column key → the treatments its cells carry. */
const alignmentMap = (columns) =>
  Object.fromEntries(columns.map((column) => [column.key, treatmentsOf(column.key)]));

/* ══════════════════════════════════════════════════════════════════════════
 * Property 19
 * ══════════════════════════════════════════════════════════════════════════ */

describe('Property 19: column alignment is determined solely by the column declaration', () => {
  afterEach(cleanup);

  it('declares exactly two alignments, and the selectors this file reads are the ones the '
    + 'component writes', () => {
    // The vocabulary and the attribute are what every clause below is stated in. Checked
    // once, here, so a run cannot pass by agreeing with a renamed attribute — and so
    // `align: 'right'` being a text column is a recorded decision rather than an accident.
    expect(COLUMN_ALIGNMENTS).toEqual(['text', 'numeric']);
    const { unmount } = mount(
      [{ key: 'pnl', header: 'P&L', align: 'numeric' }, { key: 'market', header: 'Market' }],
      [{ id: 'a', pnl: -12, market: 'BTC/USDT' }],
    );
    expect(treatmentsOf('pnl')).toEqual([NUMERIC_TREATMENT]);
    expect(treatmentsOf('market')).toEqual([TEXT_TREATMENT]);
    unmount();
  });

  it('gives every cell in a numeric column the numeric treatment, and no cell in any other '
    + 'column any part of it', () => {
    let numericColumnRuns = 0;
    let crossedValueRuns = 0;
    let notAvailableRuns = 0;

    fc.assert(
      fc.property(tableArb, ([columns, rows]) => {
        const view = mount(columns, rows);
        try {
          columns.forEach((column) => {
            const expected = treatmentFor(column.align);
            const cells = cellsOf(column.key);

            // Every row contributed a cell to this column. Without this the clause below
            // would hold vacuously for a column the component silently dropped.
            expect(cells, `column ${column.key}`).toHaveLength(rows.length);

            cells.forEach((cell) => {
              expect(
                treatmentOf(cell),
                `column ${column.key} declared align=${JSON.stringify(column.align)} rendered a `
                  + 'cell with a different alignment',
              ).toEqual(expected);
            });

            if (column.align === 'numeric') numericColumnRuns += 1;
          });

          // Witnesses for the two shapes that make the clause worth running.
          const crossed = columns.some((column) => {
            const numeric = column.align === 'numeric';
            return rows.some((row) => {
              const value = row[column.key];
              if (typeof value !== 'string') return false;
              const looksNumeric = /^[+-]?\d/.test(value.trim()) && value.trim() !== '';
              return numeric ? !looksNumeric && value.trim() !== '' : looksNumeric;
            });
          });
          if (crossed) crossedValueRuns += 1;
          if (document.querySelector('tbody .sr-only')) notAvailableRuns += 1;
        } finally {
          view.unmount();
        }
      }),
      RUNS,
    );

    // A green run over row sets that never disagreed with their columns, or over columns that
    // were never numeric, would be decoration.
    expect(numericColumnRuns, 'no numeric column was ever generated').toBeGreaterThan(0);
    expect(crossedValueRuns, 'no cell value ever disagreed with its column').toBeGreaterThan(0);
    expect(notAvailableRuns, 'the not-available marker never rendered').toBeGreaterThan(0);
  });

  it('resolves the same alignment for two independently generated row sets', () => {
    // The "not of the row, not of the value" clause stated as a comparison between two
    // renders rather than against the expectation twice: a component that read the value
    // would produce two different maps, and this fails on the difference between them even
    // if both agreed with some rule.
    let differingRowSetRuns = 0;

    fc.assert(
      fc.property(tableArb, ([columns, rowsA, rowsB]) => {
        const first = mount(columns, rowsA);
        const mapA = alignmentMap(columns);
        first.unmount();

        const second = mount(columns, rowsB);
        const mapB = alignmentMap(columns);
        second.unmount();

        columns.forEach((column) => {
          const expected = treatmentFor(column.align);
          // Same treatment in both, for as many cells as each row set has rows.
          expect(mapA[column.key]).toEqual(rowsA.map(() => expected));
          expect(mapB[column.key]).toEqual(rowsB.map(() => expected));

          /*
           * And the statement without the row counts in it: pooling both renders' cells, ONE
           * distinct treatment came out. This is the clause as "the two row sets produced one
           * alignment" — it holds over row sets of different lengths and fails if either
           * render disagreed with itself.
           */
          const pooled = [...mapA[column.key], ...mapB[column.key]];
          const distinct = [...new Set(pooled.map((entry) => JSON.stringify(entry)))];
          expect(distinct.length, `column ${column.key} rendered ${distinct.length} alignments`)
            .toBeLessThanOrEqual(1);
          if (distinct.length === 1) expect(JSON.parse(distinct[0])).toEqual(expected);
        });

        if (rowsA.length > 0 && rowsB.length > 0 && JSON.stringify(rowsA) !== JSON.stringify(rowsB)) {
          differingRowSetRuns += 1;
        }
      }),
      RUNS,
    );

    expect(differingRowSetRuns, 'the two generated row sets were never actually different')
      .toBeGreaterThan(0);
  });

  it('keeps a column\'s alignment when the column moves position in the declaration', () => {
    // Rotating the list changes every column's index, which is what `normalizeColumns` writes
    // into `column.index` and what the projected value array is keyed by. A column keyed by
    // position rather than by declaration shows up here and nowhere else.
    let rotatedRuns = 0;

    fc.assert(
      fc.property(tableArb, ([columns, rows]) => {
        if (columns.length < 2) return;
        const rotated = [...columns.slice(1), columns[0]];

        const first = mount(columns, rows);
        const before = alignmentMap(columns);
        first.unmount();

        const second = mount(rotated, rows);
        const after = alignmentMap(rotated);
        second.unmount();

        // Keyed by column, so the maps are equal even though the DOM order changed.
        expect(after).toEqual(before);
        columns.forEach((column) => {
          expect(after[column.key]).toEqual(rows.map(() => treatmentFor(column.align)));
        });
        rotatedRuns += 1;
      }),
      RUNS,
    );

    expect(rotatedRuns, 'no configuration with two or more columns was generated')
      .toBeGreaterThan(0);
  });

  it('keeps the declared alignment where a dropped column renders a second time, in the '
    + 'per-row expand', () => {
    // Requirement 17.2's reduction is the one place the alignment rule is applied twice: a
    // `priority: 3` column renders as a `<td>` (hidden by CSS below the laptop breakpoint) and
    // again as a `<dd>` inside the expand. A second copy of the rule is exactly the kind of
    // thing that drifts, and no example-based case covers it.
    let expandedRuns = 0;
    let expandedCells = 0;

    fc.assert(
      fc.property(tableArb, ([columns, rows]) => {
        const dropped = columns.filter((column) => column.priority >= DROPPED_PRIORITY);
        if (dropped.length === 0 || rows.length === 0) return;

        const view = mount(columns, rows);
        try {
          // One row's expander is enough: the clause is about the rule, not about the rows.
          const expander = document.querySelector('td[data-row-expander] button');
          expect(expander, 'a dropped column rendered no expander').not.toBeNull();
          fireEvent.click(expander);

          const detail = document.querySelector('tr[data-row-detail]');
          expect(detail, 'the expander did not reveal a detail row').not.toBeNull();

          dropped.forEach((column) => {
            const cell = detail.querySelector(`dd[data-column-key="${column.key}"]`);
            expect(cell, `dropped column ${column.key} is absent from the expand`).not.toBeNull();
            expect(
              treatmentOf(cell),
              `column ${column.key} aligns differently in the expand than in the row`,
            ).toEqual(treatmentFor(column.align));
            expandedCells += 1;
          });

          // And the `<td>` copy of the same column still carries it too.
          dropped.forEach((column) => {
            treatmentsOf(column.key).forEach((treatment) => {
              expect(treatment).toEqual(treatmentFor(column.align));
            });
          });
          expandedRuns += 1;
        } finally {
          view.unmount();
        }
      }),
      RUNS,
    );

    expect(expandedRuns, 'no run generated a dropped column with rows to expand')
      .toBeGreaterThan(0);
    expect(expandedCells).toBeGreaterThan(0);
  });

  it('aligns each header with the column it heads', () => {
    // Requirement 15.4: a right-ranged column of figures under a left-ranged header is the
    // scanning failure Requirement 11.3 is about, so the header is part of the alignment being
    // "determined by the declaration" rather than a separate decision.
    fc.assert(
      fc.property(tableArb, ([columns, rows]) => {
        const view = mount(columns, rows);
        try {
          columns.forEach((column) => {
            const head = document.querySelector(`th[data-column-key="${column.key}"]`);
            expect(head, `column ${column.key} rendered no header`).not.toBeNull();
            const expected = treatmentFor(column.align);
            expect({
              align: head.getAttribute('data-align'),
              right: head.className.includes('text-right'),
              left: head.className.includes('text-left'),
              mono: head.className.includes('font-mono'),
              tabular: head.className.includes('tabular-nums'),
            }).toEqual(expected);
          });
        } finally {
          view.unmount();
        }
      }),
      RUNS,
    );
  });
});
