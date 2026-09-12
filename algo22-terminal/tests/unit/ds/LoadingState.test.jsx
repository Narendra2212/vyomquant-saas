/**
 * `ds/LoadingState` and `ds/Skeleton` — vyomquant-ui-redesign task 6.1.
 * Requirements 14.2, 18.5.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import {
  LOADING_KINDS,
  LoadingState,
  SKELETON_GEOMETRY,
} from '../../../src/components/ds/LoadingState';
import { Skeleton } from '../../../src/components/ds/Skeleton';

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

const skeletons = () => document.querySelectorAll('.ds-skeleton');

describe('LoadingState: the seven kinds', () => {
  it('renders every declared kind, and publishes which one it rendered', () => {
    for (const kind of LOADING_KINDS) {
      const { unmount } = render(<LoadingState kind={kind} />);
      const region = screen.getByRole('status');
      expect(region.getAttribute('data-loading-kind'), kind).toBe(kind);
      expect(region.getAttribute('aria-busy')).toBe('true');
      unmount();
    }
  });

  it('throws in development for a kind it does not know', () => {
    expect(() => render(<LoadingState kind="skeleton-sparkline" />)).toThrow(/`kind` must be one of/);
    expect(() => render(<LoadingState />)).toThrow(/`kind` must be one of/);
  });

  it('degrades to inline in production rather than rendering nothing', () => {
    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<LoadingState kind="skeleton-sparkline" label="Loading fills" />);
    // `inline` claims no dimensions, so a wrong guess cannot introduce a layout shift.
    expect(screen.getByRole('status').getAttribute('data-loading-kind')).toBe('inline');
    expect(screen.getByText('Loading fills')).toBeTruthy();
    spy.mockRestore();
  });
});

describe('LoadingState: sized from the row/column config the content uses (Requirement 14.2)', () => {
  it('renders one header row plus the declared body rows and columns', () => {
    render(<LoadingState kind="skeleton-table" rows={5} columns={8} />);
    // (5 body rows + 1 header row) × 8 cells.
    expect(skeletons().length).toBe(6 * 8);
  });

  it('takes its heights from the one geometry table, not from local numbers', () => {
    const { rowHeight, headerHeight, cellHeight } = SKELETON_GEOMETRY.table;
    render(<LoadingState kind="skeleton-table" rows={2} columns={3} />);

    const rows = [...document.querySelectorAll('[style*="grid-template-columns"]')];
    expect(rows.length).toBe(3);
    expect(rows[0].style.height).toBe(`${headerHeight}px`);
    expect(rows[1].style.height).toBe(`${rowHeight}px`);
    expect(rows[2].style.height).toBe(`${rowHeight}px`);
    expect([...skeletons()].every((bar) => bar.style.height === `${cellHeight}px`)).toBe(true);
  });

  it('publishes the counts it used, so a caller can see what it was sized for', () => {
    render(<LoadingState kind="skeleton-cards" rows={2} columns={3} />);
    const region = screen.getByRole('status');
    expect(region.getAttribute('data-loading-rows')).toBe('2');
    expect(region.getAttribute('data-loading-columns')).toBe('3');
    // Three shimmer bars per card, six cards.
    expect(skeletons().length).toBe(6 * 3);
  });

  it('falls back to the kind default for a count that is not a usable positive integer', () => {
    // A skeleton whose row count came from `data.length` before the read finished can
    // arrive as any of these. None of them is a reason to render an empty region.
    for (const rows of [0, -3, Number.NaN, null, undefined, 'many']) {
      const { unmount } = render(<LoadingState kind="skeleton-table" rows={rows} columns={4} />);
      expect(screen.getByRole('status').getAttribute('data-loading-rows'), String(rows)).toBe('5');
      expect(skeletons().length).toBeGreaterThan(0);
      unmount();
    }
  });

  it('clamps an absurd count rather than rendering thousands of bars', () => {
    render(<LoadingState kind="skeleton-table" rows={100000} columns={2} />);
    expect(Number(screen.getByRole('status').getAttribute('data-loading-rows'))).toBe(40);
  });

  it('renders the chart plot area at the height ds/Chart will occupy', () => {
    render(<LoadingState kind="skeleton-chart" />);
    const bars = [...skeletons()];
    expect(bars[0].style.height).toBe(`${SKELETON_GEOMETRY.chart.height}px`);
  });

  it('renders a metric at the tier-1 label-over-figure geometry', () => {
    const { labelHeight, figureHeight } = SKELETON_GEOMETRY.metric;
    render(<LoadingState kind="skeleton-metric" columns={4} />);
    const bars = [...skeletons()];
    expect(bars.length).toBe(8);
    expect(bars[0].style.height).toBe(`${labelHeight}px`);
    expect(bars[1].style.height).toBe(`${figureHeight}px`);
  });
});

describe('LoadingState: accessible naming', () => {
  it('gives skeletons a screen-reader-only name and hides every shimmer bar', () => {
    render(<LoadingState kind="skeleton-table" rows={2} columns={2} label="Loading open positions" />);
    const region = screen.getByRole('status');
    expect(region.textContent).toBe('Loading open positions');
    // "blank, blank, blank" six times is worse than one name.
    expect([...skeletons()].every((bar) => bar.getAttribute('aria-hidden') === 'true')).toBe(true);
  });

  it('shows the label as visible text for the inline kind', () => {
    render(<LoadingState kind="inline" label="Refreshing balances" />);
    expect(screen.getByText('Refreshing balances')).toBeTruthy();
    // One region, one announcement — not one per element.
    expect(document.querySelectorAll('[role="status"]').length).toBe(1);
  });

  it('falls back to a usable name when none is supplied', () => {
    render(<LoadingState kind="skeleton-chart" />);
    expect(screen.getByRole('status').textContent).toBe('Loading');
  });

  it('renders a single spinner for the button kind and no visible text', () => {
    render(<LoadingState kind="button" label="Deploying" />);
    const region = screen.getByRole('status');
    expect(region.querySelectorAll('svg.ds-spinner').length).toBe(1);
    expect(region.querySelector('.sr-only').textContent).toBe('Deploying');
  });
});

describe('Skeleton: the shimmer atom', () => {
  it('declares its animation through one shared class, not a per-instance style element', () => {
    // `SkeletonLine` in ui-legacy/primitives.jsx renders `<style>@keyframes shimmer…`
    // as a child of every bar; a 5x8 table injected forty-one copies.
    render(<LoadingState kind="skeleton-table" rows={5} columns={8} />);
    expect(document.querySelectorAll('style').length).toBe(0);
    expect(skeletons().length).toBe(48);
  });

  it('takes its size from props and carries no colour of its own', () => {
    render(<Skeleton width="60%" height={14} />);
    const bar = document.querySelector('.ds-skeleton');
    expect(bar.style.width).toBe('60%');
    expect(bar.style.height).toBe('14px');
    expect(bar.style.backgroundColor).toBe('');
    expect(bar.style.backgroundImage).toBe('');
  });

  it('renders a circle as a square with a token radius', () => {
    render(<Skeleton circle height={24} />);
    const bar = document.querySelector('.ds-skeleton');
    expect(bar.style.width).toBe('24px');
    expect(bar.style.height).toBe('24px');
    expect(bar.style.borderRadius).toBe('9999px');
  });

  it('lets a caller override anything through style, and composes className', () => {
    render(<Skeleton className="mt-2" style={{ height: '3rem' }} />);
    const bar = document.querySelector('.ds-skeleton');
    expect(bar.className).toBe('ds-skeleton mt-2');
    expect(bar.style.height).toBe('3rem');
  });
});
