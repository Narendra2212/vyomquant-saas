/**
 * `ds/ExchangeStatus` — vyomquant-ui-redesign task 6.6. Requirements 1.2, 1.4, 1.5, 14.5.
 *
 * The load-bearing case is an unmeasured latency. `get_health_status` returns
 * `exchange_api_latency_ms: null` when nothing has been measured, and `0 ms` on the
 * strength of that would be a fabricated reading a trader would act on.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import { ExchangeStatus, isMeasuredLatency } from '../../../src/components/ds/ExchangeStatus';

const root = () => document.querySelector('[data-exchange]');

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('ExchangeStatus: an unmeasured latency is never 0 ms', () => {
  it.each([
    ['null', null],
    ['undefined', undefined],
    ['NaN', Number.NaN],
    ['Infinity', Number.POSITIVE_INFINITY],
    ['a string', '35'],
    ['negative', -1],
  ])('renders not-available for %s and prints no zero reading', (_name, latencyMs) => {
    render(<ExchangeStatus exchange="binance" connectionState="connected" latencyMs={latencyMs} />);
    expect(root().getAttribute('data-latency-reported')).toBe('false');
    expect(root().textContent).not.toContain('0 ms');
    expect(root().textContent).not.toContain('ms');
    expect(screen.getByRole('img', { name: /^Latency: not available/ }).textContent.trim()).toBe('—');
  });

  it('explains why the latency is missing rather than leaving a bare dash', () => {
    render(<ExchangeStatus exchange="binance" connectionState="connected" latencyMs={null} />);
    expect(screen.getByRole('img', { name: /^Latency: not available/ }).getAttribute('title')).toMatch(
      /not a measurement of zero/,
    );
  });

  it('renders a measured latency, including a genuine sub-millisecond zero', () => {
    render(<ExchangeStatus exchange="binance" connectionState="connected" latencyMs={35} />);
    expect(root().getAttribute('data-latency-reported')).toBe('true');
    expect(root().textContent).toContain('35 ms');
    cleanup();
    // `get_health_status` does `int(measured)`, so a real 0.4ms round trip arrives as 0.
    // Suppressing a reported measurement would be a fabrication in the other direction.
    render(<ExchangeStatus exchange="paper" connectionState="connected" latencyMs={0} />);
    expect(root().getAttribute('data-latency-reported')).toBe('true');
    expect(root().textContent).toContain('0 ms');
  });

  it('agrees with its own predicate', () => {
    expect(isMeasuredLatency(0)).toBe(true);
    expect(isMeasuredLatency(35)).toBe(true);
    expect(isMeasuredLatency(null)).toBe(false);
    expect(isMeasuredLatency('35')).toBe(false);
    expect(isMeasuredLatency(-1)).toBe(false);
  });
});

describe('ExchangeStatus: an unreported connection is not a disconnection', () => {
  it('says the connection was not reported rather than claiming disconnected', () => {
    render(<ExchangeStatus exchange="binance" latencyMs={null} />);
    expect(root().getAttribute('data-connection-state')).toBe('unreported');
    expect(screen.getByText('Connection not reported')).toBeTruthy();
    expect(screen.queryByText('Disconnected')).toBeNull();
    expect(screen.queryByText('Connected')).toBeNull();
  });

  it('renders the reported state as a word beside the dot, not as a hue alone', () => {
    render(<ExchangeStatus exchange="binance" connectionState="disconnected" latencyMs={null} />);
    const badge = document.querySelector('[data-status-group]');
    expect(badge.getAttribute('data-status-group')).toBe('error');
    expect(badge.textContent.trim()).toBe('Disconnected');
    expect(badge.querySelector('span[aria-hidden="true"]')).toBeTruthy();
  });

  it('requires the venue it is describing', () => {
    expect(() => render(<ExchangeStatus connectionState="connected" latencyMs={12} />)).toThrow(
      /`exchange` is required/,
    );
  });
});

describe('ExchangeStatus: can_trade — omitted, unreported and refused are three answers', () => {
  it('renders no capability chip when the prop is omitted', () => {
    render(<ExchangeStatus exchange="binance" connectionState="connected" latencyMs={12} />);
    expect(screen.queryByText('Can trade')).toBeNull();
    expect(screen.queryByText('Cannot trade')).toBeNull();
    expect(screen.queryByText('Trade permission not reported')).toBeNull();
  });

  it('says not reported for null, and refuses for false', () => {
    render(<ExchangeStatus exchange="binance" connectionState="connected" latencyMs={12} canTrade={null} />);
    expect(screen.getByText('Trade permission not reported')).toBeTruthy();
    cleanup();
    render(<ExchangeStatus exchange="binance" connectionState="connected" latencyMs={12} canTrade={false} />);
    expect(screen.getByText('Cannot trade')).toBeTruthy();
    cleanup();
    render(<ExchangeStatus exchange="binance" connectionState="connected" latencyMs={12} canTrade />);
    expect(screen.getByText('Can trade')).toBeTruthy();
  });

  it('renders a masked account label when one is given', () => {
    render(
      <ExchangeStatus
        exchange="binance"
        connectionState="connected"
        latencyMs={12}
        accountLabel="…4821"
      />,
    );
    expect(root().textContent).toContain('…4821');
  });

  it('carries no injected keyframes and no glow', () => {
    render(<ExchangeStatus exchange="binance" connectionState="connected" latencyMs={12} canTrade />);
    expect(document.querySelectorAll('style').length).toBe(0);
    expect(root().innerHTML).not.toContain('box-shadow');
    expect(root().innerHTML).not.toContain('animate');
  });
});
