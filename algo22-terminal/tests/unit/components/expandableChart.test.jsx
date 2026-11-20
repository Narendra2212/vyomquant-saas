/**
 * ═══════════════════════════════════════════════════════════════════════════
 * expandableChart.test.jsx — CHART-MODAL-01 (task 06)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Task 06 asks for a click-to-enlarge modal on every supported graph, closable by a visible
 * control and by Escape, without duplicate chart instances or leaks — and for the enlarged
 * view to be ZOOMABLE, over a page that does not scroll underneath it.
 *
 * `ds/Chart` is MOCKED here, for two reasons rather than convenience. It imports recharts,
 * which needs a real layout engine jsdom does not have; and the thing under test is the
 * modal's lifecycle, not the drawing. The mock records every mount with the props it
 * received, which is what makes the instance-count assertions possible at all — they are the
 * requirement Task 06 states and the one a rendered chart could not demonstrate.
 *
 * The zoom is asserted in two places and both are necessary. The ARITHMETIC — every
 * off-by-one in the window — is asserted directly against `resolveWindow`, `zoomRequest` and
 * `panRequest`, because a rounded centre is a statement about numbers and reading it back out
 * of an SVG proves less about it than the numbers do. What the chart is HANDED is asserted
 * through the mock, which reports the rows and the x endpoints it was given: `ds/Chart`
 * derives every tick and every announcement from those rows, so "the labels are right at
 * this zoom level" IS "the right rows arrived, in order, under the same axis descriptors".
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react';

import { ExpandableChart } from '../../../src/components/ExpandableChart';
import {
  MIN_VISIBLE_POINTS,
  describeWindow,
  panRequest,
  resolveWindow,
  windowHasValue,
  zoomRequest,
} from '../../../src/components/ExpandableChart';
import { resetOverlayRegistry } from '../../../src/components/ds/overlayRegistry';
import {
  resetScrollLock,
  scrollLockDepth,
  useScrollLock,
} from '../../../src/hooks/useScrollLock';

/** Every `ds/Chart` MOUNT, in order, with the props it was given. */
const mounts = vi.hoisted(() => []);

/** Every `ds/Chart` RENDER, in order. The zoom moves props without remounting anything. */
const renders = vi.hoisted(() => []);

/*
  The mock records mounts in a `useEffect` with an empty dependency list, NOT in the render
  body. That distinction is the whole point of this file.

  Recording in the body counts RENDERS, and the inline chart re-renders every time `open`
  changes — so opening the modal once produced three entries rather than two, and three
  open/close cycles produced ten rather than four. The first version of this suite did
  exactly that and its instance-count assertions were measuring React's render behaviour
  instead of the modal's lifecycle. A leak test that cannot tell a re-render from a second
  instance is worse than no leak test.

  `renders` is the separate, deliberate render log, and the zoom assertions are what it
  exists for: zooming moves the window on a chart that stays mounted, so it is invisible to
  `mounts` by construction. The stub also reflects what it was handed into data attributes —
  row count and the x value at each end — which is the same `data-chart-*` idiom `ds/Chart`
  itself uses to make a decision assertable without scraping SVG.
*/
vi.mock('../../../src/components/ds/Chart', async () => {
  const { useEffect } = await import('react');
  const Chart = ({ height, data, xAxis, ...rest }) => {
    useEffect(() => {
      mounts.push({ height, data, xAxis, ...rest });
      // eslint-disable-next-line react-hooks/exhaustive-deps -- mount only, by design
    }, []);
    renders.push({ height, data, xAxis, ...rest });
    const rows = Array.isArray(data) ? data : [];
    const edge = (row) => (row && xAxis ? String(row[xAxis.key]) : '');
    return (
      <div
        data-testid="chart-stub"
        data-height={height}
        data-rows={String(rows.length)}
        data-x-first={edge(rows[0])}
        data-x-last={edge(rows[rows.length - 1])}
        data-x-label={xAxis?.label ?? ''}
      />
    );
  };
  return { Chart, default: Chart };
});

const CHART = {
  kind: 'area',
  data: [{ point: 1, equity: 100 }],
  xAxis: { key: 'point', label: 'Point in the run', format: 'number' },
  yAxis: { label: 'Equity (quote currency)', format: 'number' },
  series: [{ key: 'equity', name: 'Equity', token: 'brand' }],
  emptyMessage: 'This run produced no equity series',
};

/** A day in milliseconds, for the dated fixture below. */
const DAY_MS = 86_400_000;

/** An ISO date `index` days after 2024-01-01, which is what a dated run's rows carry. */
const dayOf = (index) => new Date(Date.UTC(2024, 0, 1) + index * DAY_MS).toISOString().slice(0, 10);

/**
 * A 60-point dated equity curve.
 *
 * Dated rather than numbered on purpose: with `xAxis.key = 'point'` the x value and the
 * point ordinal are the same number, so "the labels follow the window" would be true of a
 * component that reported the ordinal and never looked at the data. ISO dates make the two
 * independent, and 60 points is enough for three zoom steps (60 → 30 → 15 → 8).
 */
const LONG_ROWS = Array.from({ length: 60 }, (unused, index) => ({
  date: dayOf(index),
  equity: 10_000 + index * 25,
}));

const LONG_CHART = {
  kind: 'area',
  data: LONG_ROWS,
  xAxis: { key: 'date', label: 'Date', format: 'date' },
  yAxis: { label: 'Equity (quote currency)', format: 'number' },
  series: [{ key: 'equity', name: 'Equity', token: 'brand' }],
  emptyMessage: 'This run produced no equity series',
};

const openModal = async () => {
  fireEvent.click(screen.getByTestId('chart-expand'));
  return screen.findByTestId('chart-modal');
};

/** The enlarged chart's stub, found inside the dialog so the inline one cannot be read. */
const enlargedStub = () => within(screen.getByTestId('chart-modal')).getByTestId('chart-stub');

/** What the status line says right now. */
const zoomStatus = () => screen.getByTestId('chart-zoom-status');

describe('CHART-MODAL-01: enlarging a backtest graph', () => {
  beforeEach(() => {
    mounts.length = 0;
    renders.length = 0;
    resetOverlayRegistry();
  });
  afterEach(() => {
    cleanup();
    resetOverlayRegistry();
    resetScrollLock();
  });

  it('renders the chart inline with an enlarge control', async () => {
    render(<ExpandableChart title="Equity curve" chart={CHART} height={240} />);

    expect(await screen.findByTestId('chart-stub')).toBeDefined();
    // Named after the chart, so three of these on one page are distinguishable by a screen
    // reader rather than all announcing "Enlarge".
    expect(screen.getByLabelText('Enlarge Equity curve')).toBeDefined();
    expect(screen.queryByTestId('chart-modal')).toBeNull();
  });

  it('opens a labelled modal dialog', async () => {
    render(<ExpandableChart title="Equity curve" chart={CHART} height={240} />);
    const dialog = await openModal();

    expect(dialog.getAttribute('role')).toBe('dialog');
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    // The dialog's accessible name is the chart's name, via aria-labelledby.
    const labelId = dialog.getAttribute('aria-labelledby');
    expect(document.getElementById(labelId).textContent).toContain('Equity curve');
  });

  it('draws the enlarged copy from the same description, at a greater height', async () => {
    render(<ExpandableChart title="Equity curve" chart={CHART} height={240} />);
    await openModal();

    await waitFor(() => expect(mounts.length).toBe(2));
    const [inline, enlarged] = mounts;

    expect(inline.height).toBe(240);
    expect(enlarged.height).toBeGreaterThan(240);
    /*
      The series and axes are IDENTICAL objects, not copies. That is the property the
      props-as-data API exists for: one description of the chart, so the inline and enlarged
      renders cannot disagree about what they are drawing.
    */
    expect(enlarged.series).toBe(inline.series);
    expect(enlarged.data).toBe(inline.data);
    expect(enlarged.xAxis).toBe(inline.xAxis);
  });

  it('closes on the visible control and leaves no chart behind', async () => {
    render(<ExpandableChart title="Equity curve" chart={CHART} height={240} />);
    await openModal();

    fireEvent.click(screen.getByTestId('chart-modal-close'));

    await waitFor(() => expect(screen.queryByTestId('chart-modal')).toBeNull());
    // Task 06: no duplicate instance survives a close. One stub remains — the inline one.
    expect(screen.getAllByTestId('chart-stub')).toHaveLength(1);
  });

  it('closes on Escape', async () => {
    render(<ExpandableChart title="Equity curve" chart={CHART} height={240} />);
    const dialog = await openModal();

    fireEvent.keyDown(dialog, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByTestId('chart-modal')).toBeNull());
  });

  it('mounts exactly one enlarged chart however many times it is opened', async () => {
    render(<ExpandableChart title="Equity curve" chart={CHART} height={240} />);

    for (let i = 0; i < 3; i += 1) {
      // eslint-disable-next-line no-await-in-loop -- open/close is sequential by nature
      await openModal();
      fireEvent.click(screen.getByTestId('chart-modal-close'));
      // eslint-disable-next-line no-await-in-loop
      await waitFor(() => expect(screen.queryByTestId('chart-modal')).toBeNull());
    }

    /*
      This is the leak assertion. Three open/close cycles mount three enlarged charts and
      dispose of each, so the DOM holds exactly ONE stub — the inline chart. A modal that
      kept its chart mounted after closing, or one that stacked a new copy per open, would
      leave 2, 3 or 4 here.
    */
    expect(screen.getAllByTestId('chart-stub')).toHaveLength(1);
    expect(mounts.length).toBe(4); // 1 inline + 3 enlarged
  });

  it('releases its overlay claim on close, so another dialog can open', async () => {
    render(<ExpandableChart title="Equity curve" chart={CHART} height={240} />);
    await openModal();
    fireEvent.click(screen.getByTestId('chart-modal-close'));
    await waitFor(() => expect(screen.queryByTestId('chart-modal')).toBeNull());

    /*
      `overlayRegistry.requestOverlay` THROWS on a second concurrent overlay in development.
      So a modal that failed to release its claim would not merely leak state — it would make
      the next confirm dialog on the page raise. Reopening is the cheapest proof the claim
      was given back.
    */
    await openModal();
    expect(screen.getByTestId('chart-modal')).toBeDefined();
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * THE WINDOW, AS ARITHMETIC
 * ══════════════════════════════════════════════════════════════════════════ */

describe('CHART-MODAL-01: the zoom window', () => {
  it('shows the whole domain when nothing has been requested', () => {
    expect(resolveWindow(null, 60)).toEqual({ start: 0, end: 59, span: 60 });
  });

  it('clamps a window that no longer fits the data it is applied to', () => {
    // The case a re-run while the dialog is open creates: a window chosen over 2,000 points,
    // resolved against the 90 the new run produced.
    expect(resolveWindow({ start: 1800, span: 200 }, 90)).toEqual({ start: 0, end: 89, span: 90 });
    // And a window that still fits is moved, not resized, when it hangs off the end.
    expect(resolveWindow({ start: 50, span: 20 }, 60)).toEqual({ start: 40, end: 59, span: 20 });
  });

  it('never narrows below the floor, and never applies the floor to shorter data', () => {
    expect(resolveWindow({ start: 0, span: 1 }, 60).span).toBe(MIN_VISIBLE_POINTS);
    // Three points is three points: a floor of eight over three rows would read as a bug.
    expect(resolveWindow({ start: 0, span: 1 }, 3)).toEqual({ start: 0, end: 2, span: 3 });
  });

  it('halves the window about its own centre when zooming in', () => {
    expect(zoomRequest({ start: 0, span: 60 }, 60, 0.5)).toEqual({ start: 15, span: 30 });
    // Centred, so the midpoint of the view before the step is the midpoint after it.
    expect(zoomRequest({ start: 15, span: 30 }, 60, 0.5)).toEqual({ start: 23, span: 15 });
  });

  it('reports the full domain as `null` rather than a window covering it', () => {
    // Which is what lets the dialog hand the caller's own array back, uncopied, on reset.
    expect(zoomRequest({ start: 15, span: 30 }, 60, 2)).toBeNull();
    expect(panRequest({ start: 0, span: 60 }, 60, 1)).toBeNull();
  });

  it('pans by half a window and stops at the ends', () => {
    expect(panRequest({ start: 15, span: 30 }, 60, 1)).toEqual({ start: 30, span: 30 });
    expect(panRequest({ start: 15, span: 30 }, 60, -1)).toEqual({ start: 0, span: 30 });
    // Already at the end: clamped, not wrapped round to the start of the run.
    expect(panRequest({ start: 30, span: 30 }, 60, 1)).toEqual({ start: 30, span: 30 });
  });

  it('knows a window with nothing plottable in it', () => {
    const rows = [{ v: 1 }, { v: null }, { v: '' }, { v: '2.5' }];
    expect(windowHasValue(rows, ['v'], { start: 1, end: 2 })).toBe(false);
    // A decimal string plots, which is `ds/Chart.toFiniteNumber`'s rule and has to stay it.
    expect(windowHasValue(rows, ['v'], { start: 2, end: 3 })).toBe(true);
    expect(windowHasValue(rows, [], { start: 0, end: 3 })).toBe(false);
  });

  it('says what is on screen, in points and in x values', () => {
    const view = resolveWindow({ start: 15, span: 30 }, LONG_ROWS.length);
    const sentence = describeWindow({ view, rows: LONG_ROWS, xKey: 'date' });

    expect(sentence).toContain('Points 16–45 of 60');
    expect(sentence).toContain('2024-01-16 to 2024-02-14');
    expect(describeWindow({ view: resolveWindow(null, 60), rows: LONG_ROWS, xKey: 'date' }))
      .toContain('All 60 points');
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * ZOOMING THE ENLARGED VIEW
 * ══════════════════════════════════════════════════════════════════════════ */

describe('CHART-MODAL-01: zooming the enlarged view', () => {
  beforeEach(() => {
    mounts.length = 0;
    renders.length = 0;
    resetOverlayRegistry();
  });
  afterEach(() => {
    cleanup();
    resetOverlayRegistry();
    resetScrollLock();
  });

  it('opens at the full domain, with the caller\'s own rows', async () => {
    render(<ExpandableChart title="Equity curve" chart={LONG_CHART} height={240} />);
    await openModal();

    expect(enlargedStub().dataset.rows).toBe('60');
    expect(zoomStatus().textContent).toContain('All 60 points');
    expect(zoomStatus().dataset.zoomSpan).toBe('60');
    // Not a copy of all 60: the same array the inline chart draws.
    expect(renders.at(-1).data).toBe(LONG_ROWS);
  });

  it('narrows the rendered domain when zoomed in', async () => {
    render(<ExpandableChart title="Equity curve" chart={LONG_CHART} height={240} />);
    await openModal();

    fireEvent.click(screen.getByTestId('chart-zoom-in'));

    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('30'));
    expect(zoomStatus().dataset.zoomStart).toBe('15');
    expect(zoomStatus().dataset.zoomSpan).toBe('30');

    // A second step narrows again, from the window it was already showing.
    fireEvent.click(screen.getByTestId('chart-zoom-in'));
    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('15'));

    // And the INLINE chart is untouched at every step — Task 06 asks for an enlarged
    // zoomable view, not a card that changes under the page.
    const inline = screen.getAllByTestId('chart-stub')[0];
    expect(inline.dataset.rows).toBe('60');
    expect(inline.dataset.height).toBe('240');
  });

  it('keeps the data and the labels correct at every zoom level', async () => {
    render(<ExpandableChart title="Equity curve" chart={LONG_CHART} height={240} />);
    await openModal();

    fireEvent.click(screen.getByTestId('chart-zoom-in'));
    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('30'));

    /*
      The window is rows 15..44, so the chart must be holding exactly those rows, in order,
      with their own x values — not the first 30, not a resampled 30, and not 30 rows whose
      labels still read from the full range. `ds/Chart` derives every tick, tooltip row and
      announcement from these rows, so this is what "the labels are right when zoomed" means.
    */
    const enlarged = renders.at(-1);
    expect(enlarged.data).toHaveLength(30);
    expect(enlarged.data[0]).toBe(LONG_ROWS[15]);
    expect(enlarged.data.at(-1)).toBe(LONG_ROWS[44]);
    expect(enlargedStub().dataset.xFirst).toBe('2024-01-16');
    expect(enlargedStub().dataset.xLast).toBe('2024-02-14');

    // The axis descriptors are the caller's own objects still, so no axis can be relabelled
    // by a zoom — and the status line names the same range the axis is drawing.
    expect(enlarged.xAxis).toBe(LONG_CHART.xAxis);
    expect(enlarged.yAxis).toBe(LONG_CHART.yAxis);
    expect(enlargedStub().dataset.xLabel).toBe('Date');
    expect(zoomStatus().textContent).toBe('Points 16–45 of 60, 2024-01-16 to 2024-02-14.');
  });

  it('moves the window along the run without resizing it', async () => {
    render(<ExpandableChart title="Equity curve" chart={LONG_CHART} height={240} />);
    await openModal();

    fireEvent.click(screen.getByTestId('chart-zoom-in'));
    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('30'));

    fireEvent.click(screen.getByTestId('chart-pan-later'));
    await waitFor(() => expect(zoomStatus().dataset.zoomStart).toBe('30'));
    // Zoom without pan is zoom into the midpoint and nowhere else; this is the end of the run.
    expect(enlargedStub().dataset.xLast).toBe(dayOf(59));
    expect(enlargedStub().dataset.rows).toBe('30');

    fireEvent.click(screen.getByTestId('chart-pan-earlier'));
    await waitFor(() => expect(zoomStatus().dataset.zoomStart).toBe('15'));
    expect(enlargedStub().dataset.rows).toBe('30');
  });

  it('restores the full domain on reset', async () => {
    render(<ExpandableChart title="Equity curve" chart={LONG_CHART} height={240} />);
    await openModal();

    fireEvent.click(screen.getByTestId('chart-zoom-in'));
    fireEvent.click(screen.getByTestId('chart-zoom-in'));
    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('15'));

    fireEvent.click(screen.getByTestId('chart-zoom-reset'));

    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('60'));
    expect(zoomStatus().textContent).toContain('All 60 points');
    expect(enlargedStub().dataset.xFirst).toBe(dayOf(0));
    expect(enlargedStub().dataset.xLast).toBe(dayOf(59));
    // The caller's array identity is back, so a reset is indistinguishable from never having
    // zoomed — no `slice` of the whole run left behind.
    expect(renders.at(-1).data).toBe(LONG_ROWS);
  });

  it('zooms all the way out to the full domain as well', async () => {
    render(<ExpandableChart title="Equity curve" chart={LONG_CHART} height={240} />);
    await openModal();

    fireEvent.click(screen.getByTestId('chart-zoom-in'));
    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('30'));
    fireEvent.click(screen.getByTestId('chart-zoom-out'));

    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('60'));
    expect(renders.at(-1).data).toBe(LONG_ROWS);
  });

  it('zooms in, out and back to the full range from the keyboard alone', async () => {
    render(<ExpandableChart title="Equity curve" chart={LONG_CHART} height={240} />);
    const dialog = await openModal();

    // `+` on most layouts arrives as the shifted `=`; both are accepted, and so is the
    // numeric keypad's `+`.
    fireEvent.keyDown(dialog, { key: '+' });
    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('30'));

    fireEvent.keyDown(dialog, { key: '=' });
    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('15'));

    fireEvent.keyDown(dialog, { key: '-' });
    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('30'));

    fireEvent.keyDown(dialog, { key: '0' });
    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('60'));
  });

  it('every zoom control is reachable with the keyboard', async () => {
    render(<ExpandableChart title="Equity curve" chart={LONG_CHART} height={240} />);
    await openModal();

    const group = screen.getByTestId('chart-zoom-controls');
    // Real buttons, not pointer-only affordances: Tab reaches each one and Enter presses it.
    // The group names itself so a screen reader does not announce five unexplained buttons.
    expect(group.getAttribute('aria-label')).toBe('Zoom and pan Equity curve');
    expect(group.getAttribute('aria-describedby')).toBe(zoomStatus().id);
    const labels = within(group).getAllByRole('button').map((node) => node.textContent.trim());
    expect(labels).toEqual(['Back', 'Zoom out', 'Zoom in', 'Forward', 'Reset zoom']);

    const zoomIn = screen.getByTestId('chart-zoom-in');
    zoomIn.focus();
    expect(document.activeElement).toBe(zoomIn);
    fireEvent.click(zoomIn); // what Enter and Space do to a focused <button>
    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('30'));
  });

  it('still closes on Escape while zoomed, and reopens at the full domain', async () => {
    render(<ExpandableChart title="Equity curve" chart={LONG_CHART} height={240} />);
    const dialog = await openModal();

    fireEvent.keyDown(dialog, { key: '+' });
    await waitFor(() => expect(enlargedStub().dataset.rows).toBe('30'));

    /*
      The hazard a zoom introduces to a dialog: Escape becoming "reset the zoom" and leaving
      a trader inside a modal they asked to leave. Escape belongs to the dialog at every zoom
      level, and the window it leaves behind is not remembered either — the next open is
      about the next question.
    */
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('chart-modal')).toBeNull());

    await openModal();
    expect(enlargedStub().dataset.rows).toBe('60');
    expect(zoomStatus().textContent).toContain('All 60 points');
  });

  it('offers no zoom controls for a series too short to window', async () => {
    render(<ExpandableChart title="Equity curve" chart={CHART} height={240} />);
    await openModal();

    // One point cannot be narrowed to eight. Five controls that cannot change anything are
    // worse than none.
    expect(screen.queryByTestId('chart-zoom-controls')).toBeNull();
    expect(screen.queryByTestId('chart-zoom-status')).toBeNull();
    expect(enlargedStub().dataset.rows).toBe('1');
  });

  it('refuses a window that would leave the chart with nothing to draw', async () => {
    /*
      `lib/drawdownSeries` derives `null` for an equity point it could not read, and
      `ds/Chart` treats a chart with no finite value anywhere as a caller bug — it throws in
      development. Narrowing a window is the one way a caller can produce that from data that
      is not empty, so a narrowing step onto an all-gap window is refused and the view stays
      put, rather than zooming into a crash.
    */
    const gappy = {
      ...LONG_CHART,
      data: LONG_ROWS.map((row, index) => ({
        date: row.date,
        equity: index === 0 || index === 59 ? row.equity : null,
      })),
    };
    render(<ExpandableChart title="Drawdown curve" chart={gappy} height={240} />);
    await openModal();

    fireEvent.click(screen.getByTestId('chart-zoom-in'));

    await waitFor(() => expect(zoomStatus().dataset.zoomSpan).toBe('60'));
    expect(enlargedStub().dataset.rows).toBe('60');
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * THE PAGE BEHIND THE DIALOG
 * ══════════════════════════════════════════════════════════════════════════ */

/** A stand-in for another overlay holding the same lock — `landing/Navbar`'s menu does. */
function OtherOverlay() {
  useScrollLock(true);
  return <div data-testid="other-overlay" />;
}

describe('CHART-MODAL-01: background scroll', () => {
  beforeEach(() => {
    mounts.length = 0;
    renders.length = 0;
    resetOverlayRegistry();
  });
  afterEach(() => {
    cleanup();
    resetOverlayRegistry();
    resetScrollLock();
    delete document.documentElement.clientWidth;
  });

  it('locks the page while the dialog is open and restores it exactly on close', async () => {
    render(<ExpandableChart title="Equity curve" chart={CHART} height={240} />);
    expect(document.body.style.overflow).toBe('');

    await openModal();
    expect(document.body.style.overflow).toBe('hidden');
    expect(scrollLockDepth()).toBe(1);

    fireEvent.click(screen.getByTestId('chart-modal-close'));
    await waitFor(() => expect(screen.queryByTestId('chart-modal')).toBeNull());

    /*
      Restored to nothing, not to `visible`. An inline `overflow: visible` written back over
      an element that never had one is a new declaration that outranks the stylesheet for
      good — a restore that leaves the page subtly different from how it was found.
    */
    expect(document.body.style.overflow).toBe('');
    // And nothing else was left on `<body>` either: the declaration was removed, not
    // replaced by a computed value that would outrank the stylesheet from now on.
    expect(document.body.getAttribute('style')).toBe('');
    expect(scrollLockDepth()).toBe(0);
  });

  it('restores the page when the component unmounts while still open', async () => {
    const view = render(<ExpandableChart title="Equity curve" chart={CHART} height={240} />);
    await openModal();
    expect(document.body.style.overflow).toBe('hidden');

    // A route change, an error boundary, or a parent that stops rendering the panel: nobody
    // clicked Close, and the page must still scroll afterwards.
    view.unmount();

    expect(document.body.style.overflow).toBe('');
    expect(scrollLockDepth()).toBe(0);
  });

  it('does not clobber a lock another overlay is holding', async () => {
    const other = render(<OtherOverlay />);
    expect(document.body.style.overflow).toBe('hidden');
    expect(scrollLockDepth()).toBe(1);

    render(<ExpandableChart title="Equity curve" chart={CHART} height={240} />);
    await openModal();
    expect(scrollLockDepth()).toBe(2);

    fireEvent.click(screen.getByTestId('chart-modal-close'));
    await waitFor(() => expect(screen.queryByTestId('chart-modal')).toBeNull());

    /*
      The naive save-and-restore is correct alone and wrong in company: whichever overlay
      closes second writes back the `hidden` it read on the way in, and the page stays frozen
      with nothing on screen to blame. The dialog closing here must leave the other overlay's
      lock standing.
    */
    expect(document.body.style.overflow).toBe('hidden');
    expect(scrollLockDepth()).toBe(1);

    other.unmount();
    expect(document.body.style.overflow).toBe('');
    expect(scrollLockDepth()).toBe(0);
  });

  it('pays the vanished scrollbar back as padding, and takes it off again', async () => {
    // jsdom lays nothing out, so the scrollbar has to be stated: a 1024px window over a
    // 1000px document element is a 24px classic scrollbar.
    Object.defineProperty(document.documentElement, 'clientWidth', {
      configurable: true,
      value: window.innerWidth - 24,
    });

    render(<ExpandableChart title="Equity curve" chart={CHART} height={240} />);
    await openModal();

    /*
      Without this the whole results page widens by the scrollbar's width the instant the
      dialog opens and narrows again when it closes — every panel, table and chart behind the
      backdrop shifting sideways because a trader wanted to read one chart more closely.
    */
    expect(document.body.style.paddingRight).toBe('24px');

    fireEvent.click(screen.getByTestId('chart-modal-close'));
    await waitFor(() => expect(screen.queryByTestId('chart-modal')).toBeNull());

    expect(document.body.style.paddingRight).toBe('');
    expect(document.body.getAttribute('style')).toBe('');
  });

  it('adds no padding when there is no classic scrollbar to replace', async () => {
    // Overlay scrollbars (macOS, Windows 11's auto-hiding ones) take no width, so there is
    // nothing to pay back and nothing to add.
    render(<ExpandableChart title="Equity curve" chart={CHART} height={240} />);
    await openModal();

    expect(document.body.style.paddingRight).toBe('');
    expect(document.body.style.overflow).toBe('hidden');
  });
});
