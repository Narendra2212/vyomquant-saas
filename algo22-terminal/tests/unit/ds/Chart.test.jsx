/**
 * `ds/Chart` — vyomquant-ui-redesign task 6.18. Requirements 1.5, 15.5, 18.1.
 *
 * Task 6.19 owns Property 30 ("charts label both axes and show a legend exactly when
 * multi-series") over generated series configurations. These are the example-level
 * tests that sit under it: the two required-axis-label throws, the `legend="auto"`
 * boundary at exactly 1 and 2 series, the keyboard cursor's movement and what it
 * announces, and the two Requirement 1.5 absences (no gradient, no glow).
 *
 * The shape of `EQUITY` is `Portfolio.jsx`'s current `equityCurve` and the shape of
 * `SIGNED` is the P&L-per-day series `Dashboard` will want, so the component is
 * exercised against what tasks 14.x and 16.x will hand it.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import {
  AXIS_FORMATS,
  Chart,
  CHART_KINDS,
  describePoint,
  formatAxisValue,
  hasChartData,
  LEGEND_THRESHOLD,
  PAGE_STEP,
  SERIES_PALETTE,
  SERIES_TOKENS,
  showsLegend,
} from '../../../src/components/ds/Chart';
import { statusToken } from '../../../src/design/semantic';
import { token } from '../../../src/design/tokens';

/** `Portfolio.jsx`'s equity curve: a date string x and one money series. */
const EQUITY = Object.freeze([
  { date: '2024-03-11', equity: 100000, drawdown: 0 },
  { date: '2024-03-12', equity: 101250.5, drawdown: -1.2 },
  { date: '2024-03-13', equity: 99800, drawdown: -3.4 },
  { date: '2024-03-14', equity: 104010.75, drawdown: 0 },
]);

const X_AXIS = Object.freeze({ key: 'date', label: 'Date', format: 'date' });
const Y_AXIS = Object.freeze({ label: 'Equity (USDT)', format: 'currency' });
const ONE_SERIES = Object.freeze([{ key: 'equity', name: 'Equity', token: 'brand' }]);
const TWO_SERIES = Object.freeze([
  { key: 'equity', name: 'Equity', token: 'brand' },
  { key: 'drawdown', name: 'Drawdown', token: 'error' },
]);

const BASE = Object.freeze({
  kind: 'area',
  data: EQUITY,
  xAxis: X_AXIS,
  yAxis: Y_AXIS,
  series: ONE_SERIES,
  emptyMessage: 'No equity history for this period',
});

const mount = (props) => render(<Chart {...BASE} {...props} />);

/** The data cursor — a slider, because its value is a position along the x axis. */
const cursor = () => screen.getByRole('slider');

const announcement = () => document.querySelector('[data-chart-announcement]').textContent;

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

// ---------------------------------------------------------------------------
// The vocabulary
// ---------------------------------------------------------------------------

describe('Chart: the declared vocabulary', () => {
  it('offers exactly the three kinds design.md §11.4 names', () => {
    expect(CHART_KINDS).toEqual(['area', 'line', 'bar']);
  });

  it('takes every series colour from semantic.js, and no colour prop exists', () => {
    expect(SERIES_TOKENS).toEqual(['brand', 'profit', 'loss', 'error', 'warning', 'neutral']);
    // The five status entries are `statusToken` verbatim — a drawdown curve is the
    // same red as a failed deployment badge, by construction.
    for (const name of ['profit', 'loss', 'error', 'warning', 'neutral']) {
      expect(SERIES_PALETTE[name]).toEqual(statusToken(name));
    }
    // `brand` is the neutral series hue and is `--color-brand`, not a new value.
    expect(SERIES_PALETTE.brand.fg).toBe(token.brand.base);
  });

  it('declares the axis formats a trading chart needs', () => {
    expect(AXIS_FORMATS).toEqual([
      'text', 'number', 'currency', 'percent', 'date', 'datetime', 'time',
    ]);
  });
});

// ---------------------------------------------------------------------------
// Requirement 15.5 — both axis labels are required props
// ---------------------------------------------------------------------------

describe('Chart: Requirement 15.5 — a chart cannot be rendered without both axis labels', () => {
  it('throws in development without `xAxis.label`', () => {
    expect(() => mount({ xAxis: { key: 'date', format: 'date' } })).toThrow(/xAxis\.label.*REQUIRED/s);
  });

  it('throws in development without `yAxis.label`', () => {
    expect(() => mount({ yAxis: { format: 'currency' } })).toThrow(/yAxis\.label.*REQUIRED/s);
  });

  it('throws for a blank label, not only a missing one', () => {
    expect(() => mount({ xAxis: { key: 'date', label: '   ' } })).toThrow(/xAxis\.label/);
  });

  it('names the offending axis rather than reporting "something is wrong"', () => {
    expect(() => mount({ yAxis: { label: '' } })).toThrow(/yAxis\.label/);
    expect(() => mount({ yAxis: { label: '' } })).not.toThrow(/xAxis\.label/);
  });

  it('renders both labels once they are given, and exposes them as attributes', () => {
    const { container } = mount();
    const figure = container.querySelector('figure');
    expect(figure.getAttribute('data-chart-x-label')).toBe('Date');
    expect(figure.getAttribute('data-chart-y-label')).toBe('Equity (USDT)');
    // And visibly, on the axes recharts draws.
    expect(screen.getByText('Date')).toBeTruthy();
    expect(screen.getByText('Equity (USDT)')).toBeTruthy();
  });

  it('logs instead of throwing in production, and still renders', () => {
    vi.stubEnv('DEV', false);
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { container } = mount({ xAxis: { key: 'date' } });
    expect(logged).toHaveBeenCalled();
    expect(container.querySelector('figure')).toBeTruthy();
    logged.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// Requirement 15.5 — legend="auto" is the condition, as code
// ---------------------------------------------------------------------------

describe('Chart: Requirement 15.5 — legend="auto" shows the legend iff series.length > 1', () => {
  it('is the biconditional at the boundary, as a function', () => {
    expect(LEGEND_THRESHOLD).toBe(1);
    expect(showsLegend('auto', 1)).toBe(false);
    expect(showsLegend('auto', 2)).toBe(true);
    expect(showsLegend('auto', 0)).toBe(false);
    expect(showsLegend('auto', 7)).toBe(true);
  });

  it('renders NO legend for exactly one series', () => {
    const { container } = mount({ series: ONE_SERIES });
    expect(container.querySelector('figure').getAttribute('data-chart-legend')).toBe('false');
    expect(container.querySelector('[data-chart-legend-list]')).toBeNull();
    expect(screen.queryByRole('list')).toBeNull();
  });

  it('renders a legend for exactly two series', () => {
    const { container } = mount({ series: TWO_SERIES });
    expect(container.querySelector('figure').getAttribute('data-chart-legend')).toBe('true');
    const items = screen.getByRole('list').querySelectorAll('li');
    expect([...items].map((li) => li.textContent)).toEqual(['Equity', 'Drawdown']);
  });

  it('names each series in the legend, so colour is never the only cue', () => {
    mount({ series: TWO_SERIES });
    const swatches = document.querySelectorAll('[data-series-token]');
    expect([...swatches].map((s) => s.getAttribute('data-series-token'))).toEqual(['brand', 'error']);
    // Every swatch is decorative; the name beside it carries the meaning.
    for (const swatch of swatches) expect(swatch.getAttribute('aria-hidden')).toBe('true');
  });

  it('honours an explicit override in both directions', () => {
    const single = mount({ series: ONE_SERIES, legend: true });
    expect(single.container.querySelector('[data-chart-legend-list]')).toBeTruthy();
    cleanup();
    const multi = mount({ series: TWO_SERIES, legend: false });
    expect(multi.container.querySelector('[data-chart-legend-list]')).toBeNull();
  });

  it('rejects a legend mode it does not understand', () => {
    expect(() => mount({ legend: 'always' })).toThrow(/`legend` must be 'auto', true or false/);
  });
});

// ---------------------------------------------------------------------------
// Requirements 15.5 / 18.1 — the tooltip is reachable by keyboard
// ---------------------------------------------------------------------------

describe('Chart: Requirements 15.5, 18.1 — the data cursor', () => {
  it('is a real tab stop with the slider contract fully populated', () => {
    mount();
    const slider = cursor();
    expect(slider.getAttribute('tabindex')).toBe('0');
    expect(slider.getAttribute('aria-orientation')).toBe('horizontal');
    expect(slider.getAttribute('aria-valuemin')).toBe('0');
    expect(slider.getAttribute('aria-valuemax')).toBe(String(EQUITY.length - 1));
    expect(slider.getAttribute('aria-valuenow')).toBe('0');
  });

  it('announces nothing until it is focused', () => {
    mount();
    expect(announcement()).toBe('');
  });

  it('announces the axis, the value and the position on focus', () => {
    mount();
    fireEvent.focus(cursor());
    expect(announcement()).toBe('Date 2024-03-11. Equity 100,000. Point 1 of 4.');
  });

  it('moves one point per arrow key, in both directions', () => {
    mount();
    const slider = cursor();
    fireEvent.focus(slider);
    fireEvent.keyDown(slider, { key: 'ArrowRight' });
    expect(slider.getAttribute('aria-valuenow')).toBe('1');
    expect(announcement()).toBe('Date 2024-03-12. Equity 101,250.5. Point 2 of 4.');
    fireEvent.keyDown(slider, { key: 'ArrowLeft' });
    expect(slider.getAttribute('aria-valuenow')).toBe('0');
  });

  it('treats ArrowUp / ArrowDown as the slider role does', () => {
    mount();
    const slider = cursor();
    fireEvent.focus(slider);
    fireEvent.keyDown(slider, { key: 'ArrowUp' });
    expect(slider.getAttribute('aria-valuenow')).toBe('1');
    fireEvent.keyDown(slider, { key: 'ArrowDown' });
    expect(slider.getAttribute('aria-valuenow')).toBe('0');
  });

  it('clamps at both ends rather than wrapping', () => {
    mount();
    const slider = cursor();
    fireEvent.focus(slider);
    fireEvent.keyDown(slider, { key: 'ArrowLeft' });
    expect(slider.getAttribute('aria-valuenow')).toBe('0');
    fireEvent.keyDown(slider, { key: 'End' });
    expect(slider.getAttribute('aria-valuenow')).toBe('3');
    fireEvent.keyDown(slider, { key: 'ArrowRight' });
    expect(slider.getAttribute('aria-valuenow')).toBe('3');
    fireEvent.keyDown(slider, { key: 'Home' });
    expect(slider.getAttribute('aria-valuenow')).toBe('0');
  });

  it('moves by PAGE_STEP on PageUp / PageDown, clamped', () => {
    mount();
    const slider = cursor();
    fireEvent.focus(slider);
    expect(PAGE_STEP).toBeGreaterThan(1);
    fireEvent.keyDown(slider, { key: 'PageUp' });
    // Four points, so a ten-point stride lands on the last one.
    expect(slider.getAttribute('aria-valuenow')).toBe('3');
    fireEvent.keyDown(slider, { key: 'PageDown' });
    expect(slider.getAttribute('aria-valuenow')).toBe('0');
  });

  it('carries the announcement on aria-valuetext as well as in the live region', () => {
    mount();
    const slider = cursor();
    fireEvent.focus(slider);
    fireEvent.keyDown(slider, { key: 'ArrowRight' });
    expect(slider.getAttribute('aria-valuetext')).toBe(announcement());
  });

  it('announces every series at the cursor, gaps included', () => {
    mount({
      series: TWO_SERIES,
      data: [
        { date: '2024-03-11', equity: 100000 },
        { date: '2024-03-12', equity: 101000, drawdown: -1.2 },
      ],
    });
    const slider = cursor();
    fireEvent.focus(slider);
    // A series with no value at this point is spoken as "not available", not
    // dropped — a series that vanishes from the announcement reads as absent.
    expect(announcement()).toBe(
      'Date 2024-03-11. Equity 100,000. Drawdown not available. Point 1 of 2.',
    );
  });

  it('lets Escape through, so a chart inside a dialog does not swallow the close', () => {
    mount();
    const slider = cursor();
    fireEvent.focus(slider);
    const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    slider.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });

  it('prevents the arrow keys, which would otherwise scroll the page', () => {
    mount();
    const slider = cursor();
    fireEvent.focus(slider);
    for (const key of ['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown']) {
      const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
      slider.dispatchEvent(event);
      expect(event.defaultPrevented, `${key} should be prevented`).toBe(true);
    }
  });

  it('shows the tooltip on focus and withdraws it on blur', () => {
    mount();
    const slider = cursor();
    expect(document.querySelector('[data-chart-tooltip]')).toBeNull();
    fireEvent.focus(slider);
    const panel = document.querySelector('[data-chart-tooltip]');
    expect(panel).toBeTruthy();
    expect(panel.textContent).toContain('Date 2024-03-11');
    expect(panel.textContent).toContain('100,000');
    fireEvent.blur(slider);
    expect(document.querySelector('[data-chart-tooltip]')).toBeNull();
    expect(announcement()).toBe('');
  });

  it('keeps announcing with tooltip={false} — the panel is optional, the announcement is not', () => {
    mount({ tooltip: false });
    const slider = cursor();
    fireEvent.focus(slider);
    expect(document.querySelector('[data-chart-tooltip]')).toBeNull();
    expect(announcement()).toBe('Date 2024-03-11. Equity 100,000. Point 1 of 4.');
  });

  it('does not let the pointer overlay steal hover from the chart underneath', () => {
    mount();
    expect(cursor().className).toContain('pointer-events-none');
  });
});

// ---------------------------------------------------------------------------
// Emptiness belongs to the Panel
// ---------------------------------------------------------------------------

describe('Chart: an empty chart is the Panel\'s empty state, not empty axes', () => {
  it('knows that a row array alone is not data', () => {
    expect(hasChartData([], ONE_SERIES)).toBe(false);
    expect(hasChartData(null, ONE_SERIES)).toBe(false);
    expect(hasChartData(EQUITY, [])).toBe(false);
    expect(hasChartData(EQUITY, ONE_SERIES)).toBe(true);
    // A mistyped series key presents exactly as no data, which is the point.
    expect(hasChartData(EQUITY, [{ key: 'equtiy' }])).toBe(false);
    // Zero is a value, not an absence (Requirement 14.5).
    expect(hasChartData([{ n: 0 }], [{ key: 'n' }])).toBe(true);
  });

  it('throws in development rather than drawing labelled axes over nothing', () => {
    expect(() => mount({ data: [] })).toThrow(/nothing to plot/);
    expect(() => mount({ data: [] })).toThrow(/usePanelState/);
  });

  it('renders the message and NO axes in production', () => {
    vi.stubEnv('DEV', false);
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { container } = mount({ data: [] });
    expect(container.querySelector('figure').getAttribute('data-chart-empty')).toBe('true');
    expect(screen.getByText('No equity history for this period')).toBeTruthy();
    // No plot, no axes, no cursor.
    expect(container.querySelector('svg')).toBeNull();
    expect(screen.queryByRole('slider')).toBeNull();
    logged.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// Requirement 1.5 — calm by default
// ---------------------------------------------------------------------------

describe('Chart: Requirement 1.5 — no gradients, no glow', () => {
  it('fills an area with a flat wash and defines no gradient', () => {
    const { container } = mount({ kind: 'area' });
    expect(container.querySelector('linearGradient')).toBeNull();
    expect(container.querySelector('radialGradient')).toBeNull();
    // No paint server is referenced anywhere. `url(#…)` also appears in recharts'
    // `clip-path`, which is legitimate, so the claim is made against the two
    // attributes that carry colour rather than against the whole markup.
    for (const node of container.querySelectorAll('[fill],[stroke]')) {
      expect(node.getAttribute('fill') ?? '').not.toContain('url(#');
      expect(node.getAttribute('stroke') ?? '').not.toContain('url(#');
    }
    const area = container.querySelector('.recharts-area-area');
    expect(area.getAttribute('fill')).toBe(SERIES_PALETTE.brand.wash);
    expect(area.getAttribute('fill-opacity')).toBe('1');
  });

  it('draws no shadow or glow filter on the plot', () => {
    const { container } = mount();
    expect(container.querySelector('filter')).toBeNull();
    expect(container.innerHTML).not.toContain('drop-shadow');
  });
});

// ---------------------------------------------------------------------------
// The three kinds, and formatting
// ---------------------------------------------------------------------------

describe('Chart: kinds and formatting', () => {
  it('renders each declared kind', () => {
    for (const kind of CHART_KINDS) {
      const { container } = mount({ kind });
      expect(container.querySelector('figure').getAttribute('data-chart-kind')).toBe(kind);
      expect(container.querySelector('svg')).toBeTruthy();
      cleanup();
    }
  });

  it('rejects a kind it does not understand', () => {
    expect(() => mount({ kind: 'candlestick' })).toThrow(/`kind` must be one of/);
  });

  it('requires a non-empty series list', () => {
    expect(() => mount({ series: [] })).toThrow(/`series` must be a non-empty array/);
  });

  it('groups money without rounding it, and never localises the separators', () => {
    expect(formatAxisValue(1234567.891, 'currency')).toBe('1,234,567.891');
    // A server decimal string keeps its trailing zero.
    expect(formatAxisValue('1250.50', 'currency')).toBe('1,250.50');
    expect(formatAxisValue(-1200, 'number')).toBe('-1,200');
    expect(formatAxisValue(-3.4, 'percent')).toBe('-3.4%');
  });

  it('formats time in UTC to the second, so two offices read one backtest alike', () => {
    expect(formatAxisValue('2024-03-11T12:04:05Z', 'date')).toBe('2024-03-11');
    expect(formatAxisValue('2024-03-11T12:04:05Z', 'datetime')).toBe('2024-03-11 12:04:05Z');
    expect(formatAxisValue('2024-03-11T12:04:05Z', 'time')).toBe('12:04:05Z');
  });

  it('returns null for anything with no honest rendering, never 0 and never blank', () => {
    for (const value of [null, undefined, '', '   ', Number.NaN, {}]) {
      expect(formatAxisValue(value, 'currency')).toBeNull();
    }
    expect(formatAxisValue('not a date', 'date')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The announcement, directly
// ---------------------------------------------------------------------------

describe('Chart: describePoint', () => {
  it('names the x axis, not just its value', () => {
    expect(
      describePoint({
        row: EQUITY[1],
        index: 1,
        count: 4,
        xAxis: { key: 'date', label: 'Date', format: 'date' },
        yAxis: { label: 'Equity (USDT)', format: 'currency' },
        series: [{ key: 'equity', name: 'Equity' }],
      }),
    ).toBe('Date 2024-03-12. Equity 101,250.5. Point 2 of 4.');
  });

  it('reads every series, in declared order', () => {
    expect(
      describePoint({
        row: EQUITY[2],
        index: 2,
        count: 4,
        xAxis: { key: 'date', label: 'Date', format: 'date' },
        yAxis: { label: 'Equity (USDT)', format: 'currency' },
        series: [{ key: 'equity', name: 'Equity' }, { key: 'drawdown', name: 'Drawdown' }],
      }),
    ).toBe('Date 2024-03-13. Equity 99,800. Drawdown -3.4. Point 3 of 4.');
  });
});

// ---------------------------------------------------------------------------
// Lazy loading
// ---------------------------------------------------------------------------

describe('Chart: the lazy-loading contract (design.md §13.3)', () => {
  it('has a default export, which is what React.lazy needs', async () => {
    const module = await import('../../../src/components/ds/Chart');
    expect(module.default).toBe(module.Chart);
    expect(typeof module.default).toBe('function');
  });

  it('pins the set of modules that import recharts, so a fifth cannot appear', async () => {
    const { readFileSync } = await import('node:fs');
    const path = await import('node:path');
    const { SRC, collect, stripComments } = await import('../guards/source-scan.js');

    const importers = collect(SRC, ['.js', '.jsx'])
      .filter((file) => /from\s+['"]recharts['"]/.test(stripComments(readFileSync(file, 'utf8'))))
      .map((file) => path.relative(SRC, file).split(path.sep).join('/'))
      .sort();

    /*
     * A static import anywhere in the entry graph hoists `vendor-recharts` into it,
     * so the §13.3 split only pays off once this list is one entry long. The remaining
     * call site belongs to a page task (25.1 for `PaperTrading`; `ResearchConsole` is
     * named by no task in `tasks.md`) and was out of scope for 6.18, so the list is
     * pinned by value rather than asserted down to one: it fails if a NEW importer
     * appears, and it fails again when a page migrates and the list is not lowered with
     * it.
     */
    /*
     * Seven when 6.18 pinned it, now four. Every count in this block — and the "a fifth
     * cannot appear" in the title — is a count of ENTRIES, `ds/Chart` itself included,
     * so the number here and the number in `toHaveLength` below are the same number.
     * The earlier revisions of this comment alternated between entries and "importers
     * other than `ds/Chart`", which is how the title kept saying "sixth" against a
     * five-entry list.
     *
     * `pages/Portfolio.jsx` left this list at task 16.2: its three charts now go through
     * `ds/Chart`, imported with `lazy(() => import(...))` rather than statically, so
     * recharts is no longer in that route's static graph.
     *
     * `components/DashboardUpgrades.jsx` left it at task 19.4, which deleted the file —
     * 786 lines of gamified upgrade prompts that no module imported, so its
     * `AreaChart`/`ReferenceLine` import was holding `vendor-recharts` in the entry
     * graph for markup that never rendered (design.md §7.1, Requirement 1.5). That is
     * the first entry to leave by deletion rather than by migrating to `ds/Chart`.
     *
     * `pages/Backtester.jsx` left it at task 23.2, which rebuilt the result region on
     * §7.4's three tiers: both curves are `ds/Chart`s behind
     * `lazy(() => import('../components/ds/Chart'))` and its own `Suspense` boundary, so
     * `/app/backtest` and `/app/backtester` no longer pull `vendor-recharts` into the
     * static graph. That is the third migration of the same shape, and it left the two
     * Backtester suites' `vi.mock('recharts', …)` unable to satisfy the lazy chunk — the
     * same consequence task 19.1b recorded for `dashboard_phase2a_ui.test.jsx`, resolved
     * the same way: stub the module the page imports.
     *
     * `pages/Dashboard.jsx` left it at task 19.1b, which rebuilt tier 2 and moved the
     * equity curve onto `const Chart = lazy(() => import('../components/ds/Chart'))`
     * behind its own `Suspense` boundary — the same migration Portfolio made, on the
     * page §13.3 names first. What it buys is the whole point of the split: recharts is
     * ~350KB before gzip, `vite.config.js` already routes it to `vendor-recharts`, and a
     * static import ANYWHERE in the entry graph hoists that chunk into it however the
     * chunk config reads. Dashboard is `/app/dashboard` and — until task 20.1 gives that
     * route its own page — `/app/live-trading` too, so its static import was the one
     * charging every non-charting route (Strategies, Trade History, Signal Trace) for a
     * chart it never draws, which is exactly the cost §13.3 says they must not pay.
     * The three that remain are the three still to migrate.
     */
    expect(importers).toEqual([
      'components/ResearchConsole.jsx',
      'components/ds/Chart.jsx',
      'pages/PaperTrading.jsx',
    ]);
    // The count is pinned separately so that a list edited to the wrong length fails
    // on the number as well as on the members.
    expect(importers).toHaveLength(3);
  });
});
