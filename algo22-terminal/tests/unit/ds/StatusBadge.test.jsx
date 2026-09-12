/**
 * `ds/StatusBadge` — vyomquant-ui-redesign task 6.6. Requirements 1.2, 1.4, 1.5.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import {
  COLOUR_PROPS,
  NotAvailable,
  StatusBadge,
  STATUS_BADGE_SIZES,
  humaniseState,
} from '../../../src/components/ds/StatusBadge';
import { statusToken } from '../../../src/design/semantic';

const badge = () => document.querySelector('[data-status-group]');

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('StatusBadge: colour comes from the state, and only from the state', () => {
  it('resolves the hue through statusToken rather than choosing one', () => {
    render(<StatusBadge state="running" />);
    const { group, fg, wash } = statusToken('running');
    expect(group).toBe('live');
    expect(badge().getAttribute('data-status-group')).toBe('live');
    expect(badge().style.color).toBeTruthy();
    // The rendered values are the token's, not a literal of this component's.
    expect(badge().style.color).toBe(toRgb(fg));
    // The wash comes through as the token's own rgba triplet, whatever jsdom's spacing.
    expect(badge().style.backgroundColor.replace(/\s/g, '')).toBe(wash.replace(/\s/g, ''));
  });

  it('refuses every colour prop its three predecessors accepted', () => {
    // `Tag2 c="gold"` and `Badge variant="cyan"` are both in the tree today.
    expect(() => render(<StatusBadge state="running" c="profit" />)).toThrow(
      /does not accept a colour: received c/,
    );
    expect(() => render(<StatusBadge state="running" variant="cyan" />)).toThrow(
      /does not accept a colour: received variant/,
    );
    expect(COLOUR_PROPS).toContain('c');
    expect(COLOUR_PROPS).toContain('variant');
    expect(COLOUR_PROPS).toContain('color');
  });

  it('strips a colour prop instead of letting it reach the DOM in production', () => {
    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<StatusBadge state="failed" c="gold" />);
    expect(badge().getAttribute('c')).toBeNull();
    // Still the state's colour: the ignored prop changed nothing.
    expect(badge().getAttribute('data-status-group')).toBe('error');
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it('renders an unknown state calmly in the neutral group', () => {
    // `statusToken` is total, so a value the backend adds tomorrow cannot render undefined.
    render(<StatusBadge state="quiescing" />);
    expect(badge().getAttribute('data-status-group')).toBe('neutral');
    expect(screen.getByText('Quiescing')).toBeTruthy();
  });

  it('separates stopped from failed, which both predecessors painted the same red', () => {
    const { container } = render(
      <>
        <StatusBadge state="stopped" />
        <StatusBadge state="failed" />
      </>,
    );
    const groups = [...container.querySelectorAll('[data-status-group]')].map((n) =>
      n.getAttribute('data-status-group'),
    );
    expect(groups).toEqual(['neutral', 'error']);
  });
});

describe('StatusBadge: the state is never carried by colour alone', () => {
  it('always renders text', () => {
    render(<StatusBadge state="partially_filled" />);
    expect(badge().textContent.trim()).toBe('Partially filled');
  });

  it('humanises the state and keeps the DOM text in sentence case', () => {
    // Uppercasing is CSS, so a screen reader is not handed PARTIALLY FILLED.
    expect(humaniseState('partially_filled')).toBe('Partially filled');
    expect(humaniseState('RUNNING')).toBe('Running');
    expect(humaniseState('  paused  ')).toBe('Paused');
    expect(humaniseState('backtest-running')).toBe('Backtest running');
    expect(humaniseState(null)).toBe('');
    render(<StatusBadge state="running" />);
    expect(badge().className).toContain('uppercase');
  });

  it('prefers an explicit label over the humanised state', () => {
    render(<StatusBadge state="running" label="LIVE" />);
    expect(badge().textContent.trim()).toBe('LIVE');
  });

  it('requires a state or a label rather than rendering an empty coloured box', () => {
    expect(() => render(<StatusBadge />)).toThrow(/`state` is required/);
    expect(() => render(<StatusBadge state="   " />)).toThrow(/`state` is required/);
  });

  it('renders the dot as decoration beside the text, never instead of it', () => {
    render(<StatusBadge state="connected" dot />);
    const dot = badge().querySelector('span[aria-hidden="true"]');
    expect(dot).toBeTruthy();
    expect(badge().textContent.trim()).toBe('Connected');
  });

  it('rejects an unknown size', () => {
    expect(STATUS_BADGE_SIZES).toEqual(['sm', 'md']);
    expect(() => render(<StatusBadge state="running" size="xl" />)).toThrow(/`size` must be one of/);
  });
});

describe('StatusBadge: calm by default (Requirement 1.5)', () => {
  it('carries no pulse, no halo and no injected keyframes', () => {
    // StatusDot animated forever and drew a box-shadow ring; Badge's dot was animate-pulse.
    render(<StatusBadge state="live" dot />);
    expect(document.querySelectorAll('style').length).toBe(0);
    expect(badge().className).not.toContain('animate');
    expect(badge().style.boxShadow).toBe('');
    const dot = badge().querySelector('span[aria-hidden="true"]');
    expect(dot.className).not.toContain('animate');
    expect(dot.style.boxShadow).toBe('');
    expect(dot.style.animation).toBe('');
  });
});

describe('NotAvailable: an em-dash, never a zero', () => {
  it('renders the marker with an announced name and the reason', () => {
    render(<NotAvailable label="Latency" reason="Nothing measured yet." />);
    const marker = screen.getByRole('img', { name: 'Latency: not available. Nothing measured yet.' });
    expect(marker.textContent.trim()).toBe('—');
    expect(marker.getAttribute('title')).toBe('Nothing measured yet.');
    expect(marker.textContent).not.toContain('0');
  });

  it('names the value even with no reason given', () => {
    render(<NotAvailable label="Health" />);
    expect(screen.getByRole('img', { name: 'Health: not available' })).toBeTruthy();
  });
});

/** jsdom serialises an inline `color` as `rgb(...)`; the tokens are hex. */
function toRgb(hex) {
  const value = hex.replace('#', '');
  const int = parseInt(value, 16);
  return `rgb(${(int >> 16) & 255}, ${(int >> 8) & 255}, ${int & 255})`;
}
