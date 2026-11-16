/**
 * ═══════════════════════════════════════════════════════════════════════════
 * expandableChart.test.jsx — CHART-MODAL-01 (task 06)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Task 06 asks for a click-to-enlarge modal on every supported graph, closable by a visible
 * control and by Escape, without duplicate chart instances or leaks.
 *
 * `ds/Chart` is MOCKED here, for two reasons rather than convenience. It imports recharts,
 * which needs a real layout engine jsdom does not have; and the thing under test is the
 * modal's lifecycle, not the drawing. The mock records every mount with the props it
 * received, which is what makes the instance-count assertions possible at all — they are the
 * requirement Task 06 states and the one a rendered chart could not demonstrate.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

import { ExpandableChart } from '../../../src/components/ExpandableChart';
import { resetOverlayRegistry } from '../../../src/components/ds/overlayRegistry';

/** Every `ds/Chart` MOUNT, in order, with the props it was given. */
const mounts = vi.hoisted(() => []);

/*
  The mock records mounts in a `useEffect` with an empty dependency list, NOT in the render
  body. That distinction is the whole point of this file.

  Recording in the body counts RENDERS, and the inline chart re-renders every time `open`
  changes — so opening the modal once produced three entries rather than two, and three
  open/close cycles produced ten rather than four. The first version of this suite did
  exactly that and its instance-count assertions were measuring React's render behaviour
  instead of the modal's lifecycle. A leak test that cannot tell a re-render from a second
  instance is worse than no leak test.
*/
vi.mock('../../../src/components/ds/Chart', async () => {
  const { useEffect } = await import('react');
  const Chart = ({ height, ...rest }) => {
    useEffect(() => {
      mounts.push({ height, ...rest });
      // eslint-disable-next-line react-hooks/exhaustive-deps -- mount only, by design
    }, []);
    return <div data-testid="chart-stub" data-height={height} />;
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

const openModal = async () => {
  fireEvent.click(screen.getByTestId('chart-expand'));
  return screen.findByTestId('chart-modal');
};

describe('CHART-MODAL-01: enlarging a backtest graph', () => {
  beforeEach(() => {
    mounts.length = 0;
    resetOverlayRegistry();
  });
  afterEach(() => {
    cleanup();
    resetOverlayRegistry();
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
