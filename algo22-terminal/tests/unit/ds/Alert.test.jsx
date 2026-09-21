/**
 * `ds/Alert` — vyomquant-ui-redesign task 6.23.
 * Requirements 1.2, 1.4, 1.5, 3.3, 16.2, 18.4. design.md §5.2, §6.5, §9.3.
 *
 * The severity → live-region-role mapping is the load-bearing block. `role="alert"` is
 * assertive and interrupts; `role="status"` is polite. Getting that wrong in the routine
 * direction is the noise Requirement 16.2 forbids, delivered to the users least able to
 * ignore it.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { Alert, ALERT_SEVERITIES, alertRole } from '../../../src/components/ds/Alert';
import { COLOUR_PROPS } from '../../../src/components/ds/StatusBadge';

const alert = () => document.querySelector('[data-ds="alert"]');

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe('alertRole: assertive for a failure, polite for everything else', () => {
  it('interrupts for an error and for a critical condition', () => {
    expect(alertRole('error')).toBe('alert');
    expect(alertRole('critical')).toBe('alert');
  });

  it('waits for a pause on a warning, on guidance and on information', () => {
    expect(alertRole('warning')).toBe('status');
    expect(alertRole('guidance')).toBe('status');
    expect(alertRole('info')).toBe('status');
  });

  it('is total, and an unrecognised severity is neither silent nor assertive', () => {
    // Understating would announce a real failure politely; overstating would interrupt for
    // a routine message. `warning` is the middle, and it is polite.
    ['', 'nope', null, undefined, 42, {}].forEach((value) => {
      expect(alertRole(value)).toBe('status');
    });
  });

  it('answers for every declared severity', () => {
    ALERT_SEVERITIES.forEach((severity) => {
      expect(['alert', 'status']).toContain(alertRole(severity));
    });
  });
});

describe('Alert: the role rendered matches the mapping', () => {
  it.each(ALERT_SEVERITIES)('renders %s with the role alertRole declares', (severity) => {
    render(<Alert severity={severity} title="Condition" />);
    expect(alert().getAttribute('role')).toBe(alertRole(severity));
    // The role already implies the politeness; a second `aria-live` would be a second
    // place for the two to disagree.
    expect(alert().getAttribute('aria-live')).toBeNull();
  });

  it('renders the shell disconnected strip assertively (§6.5)', () => {
    render(
      <Alert severity="error" variant="strip" title="Not connected to the trading engine">
        Live positions, orders and P&amp;L below may be out of date.
      </Alert>,
    );
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(alert().getAttribute('data-alert-variant')).toBe('strip');
  });

  it('cannot have its role overridden at the call site', () => {
    // A `role` passed in is the one prop that could turn a routine message assertive.
    render(<Alert severity="info" title="Backtest complete" role="alert" />);
    expect(alert().getAttribute('role')).toBe('status');
  });
});

describe('Alert: severity chooses the treatment, and nothing else does', () => {
  it('takes its hue from statusToken, so an error and a critical share the group', () => {
    const { unmount } = render(<Alert severity="error" title="Order rejected" />);
    expect(alert().getAttribute('data-status-group')).toBe('error');
    unmount();

    render(<Alert severity="critical" title="Risk limit breached" />);
    expect(alert().getAttribute('data-status-group')).toBe('error');
  });

  it('gives an informational alert no hue at all (Requirement 1.5)', () => {
    render(<Alert severity="info" title="Backtest complete" />);
    expect(alert().getAttribute('data-status-group')).toBe('neutral');
  });

  it('distinguishes guidance from a warning on the border axis, not only the hue', () => {
    // §9.3: the dashed border and the Info icon are what make an invalid-connection
    // attempt read as "not yet" rather than "broken".
    const { unmount } = render(<Alert severity="guidance" title="Cannot connect" />);
    expect(alert().style.borderStyle).toBe('dashed');
    unmount();

    render(<Alert severity="warning" title="Validation warning" />);
    expect(alert().style.borderStyle).toBe('solid');
  });

  it('refuses a colour prop by name', () => {
    // The same list `ds/StatusBadge` refuses, so a spelling added there is refused here
    // too and there is one list rather than two (Requirement 1.4).
    expect(() => render(<Alert severity="error" title="Order rejected" color="red" />)).toThrow(
      /does not accept a colour: received color/,
    );
    expect(() => render(<Alert severity="error" title="Order rejected" c="gold" />)).toThrow(
      /does not accept a colour: received c/,
    );
    expect(() => render(<Alert severity="error" title="Order rejected" tone="loud" />)).toThrow(
      /does not accept a colour: received tone/,
    );
    COLOUR_PROPS.filter((name) => name !== 'variant').forEach((name) => {
      expect(() =>
        render(<Alert severity="error" title="Order rejected" {...{ [name]: 'x' }} />),
      ).toThrow(new RegExp(`does not accept a colour: received ${name}`));
    });
  });

  it('refuses `variant="danger"` on the geometry axis, not the colour one', () => {
    // `variant` is the one name on `ds/StatusBadge`'s list this component cannot refuse:
    // it is Alert's geometry prop (`block` | `strip`), which §6.5's shell strip and the
    // Dashboard's condition strip both pass. So it is subtracted from the refused list
    // and a colour-ish value still fails loudly — with the message that names the two
    // legal values and points at `severity` for anything to do with hue.
    expect(COLOUR_PROPS).toContain('variant');
    expect(() => render(<Alert severity="error" title="Order rejected" variant="danger" />)).toThrow(
      /`variant` must be one of block \| strip/,
    );
    // And the legal value is untouched by the subtraction.
    render(<Alert severity="error" title="Order rejected" variant="strip" />);
    expect(alert().getAttribute('data-alert-variant')).toBe('strip');
  });

  it('never lets a colour prop reach the DOM, even in production', () => {
    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<Alert severity="error" title="Order rejected" tone="loud" />);
    expect(alert().getAttribute('tone')).toBeNull();
    expect(spy).toHaveBeenCalled();
  });

  it('throws in development for an unknown severity and renders as a warning', () => {
    expect(() => render(<Alert severity="catastrophic" title="Something" />)).toThrow(
      /`severity` must be one of/,
    );

    vi.stubEnv('DEV', false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<Alert severity="catastrophic" title="Something" />);
    expect(alert().getAttribute('data-alert-severity')).toBe('warning');
    expect(alert().getAttribute('role')).toBe('status');
  });
});

describe('Alert: content', () => {
  it('renders the title and the consequence', () => {
    render(
      <Alert severity="error" title="Not connected to the trading engine">
        Live positions may be out of date.
      </Alert>,
    );
    expect(screen.getByText('Not connected to the trading engine')).toBeTruthy();
    expect(screen.getByText('Live positions may be out of date.')).toBeTruthy();
  });

  it('throws in development without a title', () => {
    expect(() => render(<Alert severity="error" />)).toThrow(/`title` is required/);
    expect(() => render(<Alert severity="error" title="   " />)).toThrow(/`title` is required/);
  });
});

describe('Alert: the action', () => {
  it('renders an action spec as a control (§6.5\'s retry)', () => {
    const onClick = vi.fn();
    render(
      <Alert severity="error" title="Not connected" action={{ label: 'Retry', onClick }} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('renders a route spec as a link (the Dashboard\'s Review)', () => {
    render(
      <MemoryRouter>
        <Alert
          severity="warning"
          title="1 exchange disconnected · 1 strategy stopped on error"
          action={{ label: 'Review', to: '/app/live' }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole('link', { name: 'Review' }).getAttribute('href')).toBe('/app/live');
  });

  it('renders a composed node as-is', () => {
    render(
      <Alert
        severity="warning"
        title="1 strategy stopped"
        action={<button type="button">Review</button>}
      />,
    );
    expect(screen.getByRole('button', { name: 'Review' })).toBeTruthy();
  });
});

describe('Alert: dismissal', () => {
  it('renders no dismiss control by default — a condition that is still true stays', () => {
    render(<Alert severity="error" title="Not connected" />);
    expect(document.querySelector('[data-ds="alert-dismiss"]')).toBeNull();
  });

  it('renders a named dismiss button when onDismiss is given', () => {
    const onDismiss = vi.fn();
    render(<Alert severity="info" title="Backtest complete" onDismiss={onDismiss} />);
    // Requirement 18.4: an icon-only control still needs an accessible name.
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('takes the caller\'s wording for that name', () => {
    render(
      <Alert
        severity="info"
        title="Backtest complete"
        onDismiss={() => {}}
        dismissLabel="Hide this notice"
      />,
    );
    expect(screen.getByRole('button', { name: 'Hide this notice' })).toBeTruthy();
  });
});
