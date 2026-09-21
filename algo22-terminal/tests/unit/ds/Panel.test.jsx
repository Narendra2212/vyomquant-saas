/**
 * `ds/Panel` — vyomquant-ui-redesign task 6.1.
 * Requirements 3.5, 3.6, 6.6, 10.4, 12.2, 14.1, 14.2, 14.3, 14.5, 19.3.
 *
 * These are the worked examples. The universal statements — that the state fully
 * determines the rendering for EVERY state and payload (P26), and that every
 * money-bearing panel carries an indicator naming its environment (P12) — belong to
 * tasks 6.2 and 6.3.
 *
 * `import.meta.env.DEV` is true under vitest, so the development-time throws are live
 * here without stubbing. The two production-degradation tests stub it to false, which
 * is how `design/errorCopy.js`'s suite exercises the same split.
 */

import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import { Panel, MONEY_CONTENT } from '../../../src/components/ds/Panel';
import {
  ALL_PANEL_STATES,
  PANEL_STATES,
  STATES_WITHOUT_CHILDREN,
} from '../../../src/hooks/usePanelState';

/** The child every state test looks for. A figure, so "no stale figure" is checkable. */
const FIGURE = '12,843.55';

const EMPTY = Object.freeze({
  headline: 'No open positions',
  body: 'Positions appear here when a deployed strategy fills an order.',
  action: { label: 'View strategies', to: '/app/strategies' },
});

function renderPanel(props) {
  return render(
    <MemoryRouter>
      <Panel {...props}>
        <p>{FIGURE}</p>
      </Panel>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('Panel: the state contract (Requirement 14.5)', () => {
  it('renders children in ready', () => {
    renderPanel({ title: 'Open positions', state: PANEL_STATES.READY });
    expect(screen.getByText(FIGURE)).toBeTruthy();
  });

  it('keeps children on screen in refreshing, with an inline progress affordance', () => {
    renderPanel({ title: 'Open positions', state: PANEL_STATES.REFRESHING });
    expect(screen.getByText(FIGURE)).toBeTruthy();
    // §11.1: a subtle inline affordance, never a skeleton over live figures.
    const progress = screen.getByRole('status');
    expect(progress.getAttribute('data-loading-kind')).toBe('inline');
    expect(progress.textContent).toContain('Refreshing Open positions');
  });

  it('renders no children in any of the hook\'s STATES_WITHOUT_CHILDREN', () => {
    // Driven by the hook's own list, so a ninth state cannot be added without this
    // covering it. Each state is given the configuration it requires.
    const configFor = (state) => ({
      title: 'Open positions',
      state,
      loading: { kind: 'skeleton-table', rows: 3, columns: 5 },
      empty: EMPTY,
      error: { error: { category: 'SERVER_ERROR' }, context: 'positions' },
      unavailable: { reason: 'Position data is not reported for this exchange connector.' },
    });

    for (const state of STATES_WITHOUT_CHILDREN) {
      const { unmount } = renderPanel(configFor(state));
      expect(screen.queryByText(FIGURE), `${state} rendered its children`).toBeNull();
      unmount();
    }
  });

  it('discards the previous payload when a successful read is followed by a failure', () => {
    // The Requirement 14.5 sequence, as one panel moving through it.
    const { rerender } = render(
      <MemoryRouter>
        <Panel title="Open positions" state={PANEL_STATES.READY}>
          <p>{FIGURE}</p>
        </Panel>
      </MemoryRouter>,
    );
    expect(screen.getByText(FIGURE)).toBeTruthy();

    rerender(
      <MemoryRouter>
        <Panel
          title="Open positions"
          state={PANEL_STATES.ERROR}
          error={{ error: { category: 'SERVER_ERROR' }, context: 'positions' }}
        >
          <p>{FIGURE}</p>
        </Panel>
      </MemoryRouter>,
    );
    expect(screen.queryByText(FIGURE)).toBeNull();
    // …and no zero stood in for the figure either.
    expect(screen.queryByText('0')).toBeNull();
    expect(screen.getByRole('alert')).toBeTruthy();
  });

  it('publishes the resolved state, so a page cannot be in two states at once', () => {
    for (const state of ALL_PANEL_STATES) {
      const { unmount } = renderPanel({
        title: 'Panel',
        state,
        loading: { kind: 'skeleton-metric' },
        empty: EMPTY,
        error: { error: null, context: 'positions' },
        unavailable: { reason: 'Not reported by this connector.' },
      });
      const panels = document.querySelectorAll('[data-panel-state]');
      expect(panels.length).toBe(1);
      expect(panels[0].getAttribute('data-panel-state')).toBe(state);
      unmount();
    }
  });

  it('renders nothing at all in idle — not an empty message', () => {
    // `idle` means no read has been issued. "You have none of these" would be a claim
    // about data nobody has looked at.
    renderPanel({ title: 'Open positions', state: PANEL_STATES.IDLE });
    expect(screen.queryByText(FIGURE)).toBeNull();
    expect(screen.queryByText(EMPTY.headline)).toBeNull();
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Open positions');
  });
});

describe('Panel: money content and the environment indicator (Requirements 7.4, 12.2)', () => {
  it('throws in development when a money panel omits environment', () => {
    expect(() => renderPanel({ title: 'Open positions', money: true, state: PANEL_STATES.READY }))
      .toThrow(/declares money content .* but omits `environment`/);
  });

  it('accepts a money panel whose server reported no environment, and says unconfirmed', () => {
    // `null` is a decision — the server did not say — and is the case Property 12's
    // second half is about. It must not throw and must not name an environment.
    renderPanel({ title: 'Open positions', money: true, environment: null, state: PANEL_STATES.READY });
    const badge = document.querySelector('[data-panel-environment]');
    expect(badge.getAttribute('data-panel-environment')).toBe('UNCONFIRMED');
    expect(badge.textContent).toContain('ENVIRONMENT UNCONFIRMED');
    expect(badge.textContent).not.toContain('LIVE');
    expect(badge.textContent).not.toContain('PAPER');
  });

  it('names the environment in the indicator\'s accessible text', () => {
    for (const [environment, label] of [['LIVE', 'LIVE'], ['PAPER', 'PAPER TRADING'], ['BACKTEST', 'BACKTEST']]) {
      const { unmount } = renderPanel({ title: 'Balances', money: true, environment, state: PANEL_STATES.READY });
      const badge = document.querySelector('[data-panel-environment]');
      expect(badge.getAttribute('data-panel-environment')).toBe(environment);
      // The prefix is what makes the announcement a sentence rather than a bare word.
      expect(badge.textContent).toBe(`Trading environment: ${label}`);
      unmount();
    }
  });

  it('does not name an environment it cannot recognise', () => {
    // A value outside the three is a value we must not guess at (design/semantic.js).
    renderPanel({ title: 'Balances', money: true, environment: 'PROD', state: PANEL_STATES.READY });
    const badge = document.querySelector('[data-panel-environment]');
    expect(badge.getAttribute('data-panel-environment')).toBe('UNCONFIRMED');
    expect(badge.textContent).not.toContain('PROD');
  });

  it('distinguishes live from paper on four axes, not on hue alone (Requirement 12.3)', () => {
    const read = (environment) => {
      const { unmount } = renderPanel({ title: 'P&L', money: true, environment, state: PANEL_STATES.READY });
      const badge = document.querySelector('[data-panel-environment]');
      const seen = {
        label: badge.textContent,
        colour: badge.style.color,
        border: badge.style.borderStyle,
        icon: badge.querySelector('svg')?.getAttribute('class'),
      };
      unmount();
      return seen;
    };
    const live = read('LIVE');
    const paper = read('PAPER');
    expect(live.label).not.toBe(paper.label);
    expect(live.colour).not.toBe(paper.colour);
    expect(live.border).not.toBe(paper.border);
    expect(live.icon).not.toBe(paper.icon);
  });

  it('renders the unconfirmed indicator rather than nothing when production degrades', () => {
    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderPanel({ title: 'Open positions', money: true, state: PANEL_STATES.READY });
    expect(spy).toHaveBeenCalled();
    // Silence would leave a trader unable to tell real orders from simulated ones.
    expect(document.querySelector('[data-panel-environment]').getAttribute('data-panel-environment'))
      .toBe('UNCONFIRMED');
    spy.mockRestore();
  });

  it('marks money panels in the DOM, and declares what counts as money content', () => {
    renderPanel({ title: 'Open positions', money: true, environment: 'LIVE', state: PANEL_STATES.READY });
    expect(document.querySelector('[data-panel-money="true"]')).toBeTruthy();
    // The vocabulary Property 12's generator reads.
    expect(MONEY_CONTENT).toContain('position');
    expect(MONEY_CONTENT).toContain('order');
    expect(MONEY_CONTENT).toContain('pnl');
  });

  it('does not require an environment on a panel that shows no money', () => {
    expect(() => renderPanel({ title: 'Recent activity', state: PANEL_STATES.READY })).not.toThrow();
    expect(document.querySelector('[data-panel-environment]')).toBeNull();
  });
});

describe('Panel: the per-state configuration each requirement demands', () => {
  it('requires loading.kind (Requirement 14.2)', () => {
    expect(() => renderPanel({ title: 'Positions', state: PANEL_STATES.LOADING }))
      .toThrow(/`loading.kind` is/);
  });

  it('passes the declared row and column config to the skeleton', () => {
    renderPanel({
      title: 'Positions',
      state: PANEL_STATES.LOADING,
      loading: { kind: 'skeleton-table', rows: 5, columns: 8 },
    });
    const region = screen.getByRole('status');
    expect(region.getAttribute('data-loading-kind')).toBe('skeleton-table');
    expect(region.getAttribute('data-loading-rows')).toBe('5');
    expect(region.getAttribute('data-loading-columns')).toBe('8');
    // Named per panel, so nine loading panels announce nine distinguishable loads.
    expect(region.textContent).toContain('Loading Positions');
  });

  it('requires an empty configuration, and EmptyState names the field that is missing', () => {
    expect(() => renderPanel({ title: 'Positions', state: PANEL_STATES.EMPTY }))
      .toThrow(/has no `empty` configuration/);
    expect(() =>
      renderPanel({
        title: 'Positions',
        state: PANEL_STATES.EMPTY,
        empty: { headline: 'No open positions', action: EMPTY.action },
      }),
    ).toThrow(/`body` is required/);
  });

  it('requires a human reason for unavailable (Requirement 19.3)', () => {
    expect(() => renderPanel({ title: 'Positions', state: PANEL_STATES.UNAVAILABLE }))
      .toThrow(/carries no reason/);
    expect(() =>
      renderPanel({ title: 'Positions', state: PANEL_STATES.UNAVAILABLE, unavailable: { reason: '   ' } }),
    ).toThrow(/carries no reason/);

    renderPanel({
      title: 'Positions',
      state: PANEL_STATES.UNAVAILABLE,
      unavailable: { reason: 'Position data is not reported for this exchange connector.' },
    });
    expect(screen.getByText('Not available')).toBeTruthy();
    expect(screen.getByText(/not reported for this exchange connector/)).toBeTruthy();
    // Not an error and not an empty: nothing failed, so nothing is announced.
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('renders unauthorised through the same single translation, with no retry', () => {
    renderPanel({ title: 'Positions', state: PANEL_STATES.UNAUTHORISED });
    expect(screen.getByText('Your session has expired')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Sign in' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();
  });

  it('falls to error, not to ready, for a state it does not recognise', () => {
    expect(() => renderPanel({ title: 'Positions', state: 'settled' })).toThrow(/`state` must be one of/);

    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderPanel({ title: 'Positions', state: 'settled' });
    // The children are the risk: an unknown state must never put a payload on screen.
    expect(screen.queryByText(FIGURE)).toBeNull();
    expect(screen.getByRole('alert')).toBeTruthy();
    spy.mockRestore();
  });
});

describe('Panel: the shell', () => {
  it('names its region with its heading', () => {
    renderPanel({ title: 'Open positions', state: PANEL_STATES.READY });
    const heading = screen.getByRole('heading', { level: 2 });
    const region = document.querySelector('[data-panel-state]');
    expect(region.getAttribute('aria-labelledby')).toBe(heading.id);
  });

  it('renders level 3 when asked, for a panel nested under a section', () => {
    renderPanel({ title: 'Fills', level: 3, state: PANEL_STATES.READY });
    expect(screen.getByRole('heading', { level: 3 }).textContent).toBe('Fills');
  });

  it('forwards unrecognised props and composes className', () => {
    renderPanel({ title: 'Fills', state: PANEL_STATES.READY, className: 'col-span-2', id: 'fills-panel' });
    const region = document.querySelector('#fills-panel');
    expect(region.className).toContain('col-span-2');
    expect(region.className).toContain('bg-surface-panel');
  });

  it('renders no header at all when there is nothing to put in one', () => {
    render(
      <MemoryRouter>
        <Panel state={PANEL_STATES.READY}><p>{FIGURE}</p></Panel>
      </MemoryRouter>,
    );
    expect(screen.queryByRole('heading')).toBeNull();
    expect(screen.getByText(FIGURE)).toBeTruthy();
  });
});

describe('Panel: refreshing without children is reported, not fatal', () => {
  let spy;
  beforeEach(() => {
    spy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('logs and still renders', () => {
    // A page writing `{rows.length > 0 && <DataTable/>}` can legitimately hand over
    // `false` for one render, so this is a report rather than a throw.
    expect(() =>
      render(
        <MemoryRouter>
          <Panel title="Positions" state={PANEL_STATES.REFRESHING}>{false}</Panel>
        </MemoryRouter>,
      ),
    ).not.toThrow();
    expect(spy.mock.calls.some(([message]) => String(message).includes('`refreshing` with no children')))
      .toBe(true);
    spy.mockRestore();
  });
});
