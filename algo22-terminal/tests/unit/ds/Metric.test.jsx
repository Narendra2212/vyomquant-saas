/**
 * `ds/Metric` — vyomquant-ui-redesign task 6.4.
 * Requirements 4.1, 8.1, 11.1, 14.5, 19.3, 1.5.
 *
 * Property 5 (declared-field rendering) is task 6.5's. The tests here are the example
 * cases for the one leaf behaviour the component exists for — **it never renders `0`
 * for a value it was not given** — plus the tier, format and colour contracts.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import { Metric, METRIC_FORMATS, NOT_AVAILABLE, formatFigure } from '../../../src/components/ds/Metric';
import { available, unavailable, UNREPORTED_REASON } from '../../../src/design/reported';
import { statusToken } from '../../../src/design/semantic';

const marker = () => document.querySelector('[data-metric-marker="not-available"]');
const root = () => document.querySelector('[data-metric-tier]');
const figureText = () => root().textContent;

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('Metric: it never renders 0 for a value it was not given (Requirements 14.5, 19.3)', () => {
  it.each([
    ['null', null],
    ['undefined', undefined],
    ['an empty string', ''],
    ['NaN', Number.NaN],
    ['Infinity', Number.POSITIVE_INFINITY],
  ])('renders the not-available marker for %s', (_name, value) => {
    render(<Metric label="Total P&L" value={value} format="currency" tier={1} />);

    expect(marker()).toBeTruthy();
    expect(figureText()).not.toMatch(/0/);
    expect(root().getAttribute('data-metric-available')).toBe('false');
  });

  it('renders a measured zero, because a flat figure is a reading', () => {
    render(<Metric label="Today's P&L" value={0} format="currency" precision={2} />);

    expect(marker()).toBeNull();
    expect(screen.getByText('0.00')).toBeTruthy();
    expect(root().getAttribute('data-metric-available')).toBe('true');
  });

  it('renders the marker for `unavailable` even when a value is in hand', () => {
    // Requirement 14.5's cached-as-live case: the page has the number and has decided
    // it must not be shown.
    render(
      <Metric
        label="Unrealised P&L"
        value={4210.5}
        unavailable
        unavailableReason="The connector stopped reporting unrealised P&L"
      />,
    );

    expect(marker()).toBeTruthy();
    expect(figureText()).not.toMatch(/4,?210/);
    expect(marker().getAttribute('title')).toBe('The connector stopped reporting unrealised P&L');
  });

  it('never renders a unit beside a missing figure', () => {
    // `— USDT` asserts that the value we do not have is a USDT amount.
    render(<Metric label="Equity" value={null} unit="USDT" />);
    expect(figureText()).not.toMatch(/USDT/);
  });

  it('refuses a non-scalar value instead of crashing the panel', () => {
    expect(() => render(<Metric label="Exposure" value={{ amount: 12 }} />))
      .toThrow(/`value` must be a number, a string, or a `Reported<T>`/);

    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<Metric label="Exposure" value={{ amount: 12 }} />);
    expect(marker()).toBeTruthy();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe('Metric: the not-available marker (design.md §5.1)', () => {
  it('is an em-dash in content-muted, named for a screen reader, reason on a tooltip', () => {
    render(
      <Metric label="Sharpe ratio" value={null} unavailableReason="No backtest has been run" />,
    );

    const node = marker();
    expect(node.textContent).toContain(NOT_AVAILABLE);
    expect(node.className).toContain('text-content-muted');
    expect(screen.getByLabelText('Sharpe ratio: not available')).toBe(node);
    expect(node.getAttribute('title')).toBe('No backtest has been run');
    // The dash itself is hidden; the sentence is what is announced.
    expect(node.querySelector('[aria-hidden="true"]').textContent).toBe(NOT_AVAILABLE);
    expect(node.querySelector('.sr-only').textContent)
      .toBe('Sharpe ratio: not available. No backtest has been run');
  });

  it('always carries a reason, even when the call site gave none', () => {
    render(<Metric label="Drawdown" value={null} />);
    expect(marker().getAttribute('title')).toBe(UNREPORTED_REASON);
  });

  it('keeps the label visible whether or not the value arrived', () => {
    render(<Metric label="Win rate" value={null} format="percent" />);
    expect(screen.getByText('Win rate')).toBeTruthy();
  });

  it('requires a label — a bare figure is a number nobody can identify', () => {
    expect(() => render(<Metric value={12} />)).toThrow(/`label` is required/);
  });
});

describe('Metric: Reported<T> (design.md §18)', () => {
  it('renders the available arm', () => {
    render(<Metric label="Positions" value={available(7)} format="integer" />);
    expect(screen.getByText('7')).toBeTruthy();
  });

  it('renders the unavailable arm\'s own reason', () => {
    render(
      <Metric
        label="Estimated exposure"
        value={unavailable('Sizing is not configured for this deployment')}
      />,
    );
    expect(marker().getAttribute('title')).toBe('Sizing is not configured for this deployment');
  });

  it('renders a Reported zero as zero', () => {
    render(<Metric label="Open orders" value={available(0)} format="integer" />);
    expect(screen.getByText('0')).toBeTruthy();
    expect(marker()).toBeNull();
  });
});

describe('Metric: tiers and formats', () => {
  it.each([
    [1, 'text-figure'],
    [2, 'text-title'],
    [3, 'text-body'],
  ])('tier %i selects %s', (tier, expected) => {
    render(<Metric label="Portfolio value" value={12843.55} tier={tier} />);
    expect(screen.getByText('12,843.55').className).toContain(expected);
    expect(root().getAttribute('data-metric-tier')).toBe(String(tier));
  });

  it('rejects a tier outside the three', () => {
    expect(() => render(<Metric label="Value" value={1} tier={4} />)).toThrow(/`tier` must be 1, 2 or 3/);
  });

  it('declares exactly design.md §5.1\'s formats and rejects anything else', () => {
    expect(METRIC_FORMATS).toEqual(['currency', 'percent', 'number', 'integer', 'duration', 'raw']);
    expect(() => render(<Metric label="Value" value={1} format="money" />))
      .toThrow(/`format` must be one of/);
  });

  it('groups and never rounds unless asked', () => {
    expect(formatFigure(1234567.891, { format: 'currency' })).toBe('1,234,567.891');
    expect(formatFigure('1.50', { format: 'currency' })).toBe('1.50');
    expect(formatFigure(1234.5678, { format: 'currency', precision: 2 })).toBe('1,234.57');
    expect(formatFigure(-1200, { format: 'number' })).toBe('-1,200');
  });

  it('appends % without multiplying, so a 3.2% drawdown is not rendered as 320%', () => {
    expect(formatFigure(3.2, { format: 'percent' })).toBe('3.2%');
    render(<Metric label="Current drawdown" value={3.2} format="percent" />);
    expect(screen.getByText('3.2%')).toBeTruthy();
  });

  it('rounds an integer count and leaves raw text alone', () => {
    expect(formatFigure(12.7, { format: 'integer' })).toBe('13');
    expect(formatFigure('v3', { format: 'raw' })).toBe('v3');
  });

  it('reads a duration in milliseconds and carries its own unit', () => {
    expect(formatFigure(842, { format: 'duration' })).toBe('842ms');
    expect(formatFigure(12_400, { format: 'duration' })).toBe('12.4s');
    expect(formatFigure(247_000, { format: 'duration' })).toBe('4m 07s');
  });

  it('renders numeric figures in mono with tabular numerals', () => {
    render(<Metric label="Price" value={64200.5} format="currency" />);
    const figure = screen.getByText('64,200.5');
    expect(figure.className).toContain('font-mono');
    expect(figure.className).toContain('tabular-nums');
  });
});

describe('Metric: colour is opt-in (Requirement 1.5)', () => {
  it('carries no hue at all without a `state`', () => {
    render(<Metric label="Portfolio value" value={12843.55} tier={1} />);
    const figure = screen.getByText('12,843.55');
    expect(figure.style.color).toBe('');
    expect(figure.className).toContain('text-content-primary');
    expect(root().hasAttribute('data-metric-state')).toBe(false);
  });

  it('takes its hue from statusToken when a state is named, and never from a colour prop', () => {
    render(<Metric label="Deployment" value="Running" format="raw" state="running" />);
    const figure = screen.getByText('Running');
    expect(figure.style.color).toBeTruthy();
    expect(root().getAttribute('data-metric-state')).toBe(statusToken('running').group);
  });
});

describe('Metric: the hint', () => {
  it('puts the hint on a tooltip and in the accessible description', () => {
    render(
      <Metric label="Total P&L" value={1} hint="Realised + unrealised since account open" />,
    );
    const label = screen.getByText('Total P&L');
    expect(label.getAttribute('title')).toBe('Realised + unrealised since account open');
    expect(document.getElementById(label.getAttribute('aria-describedby')).textContent)
      .toBe('Realised + unrealised since account open');
  });
});
