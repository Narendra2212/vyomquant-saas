/**
 * `builder/validationSurfaces` — vyomquant-ui-redesign task 24.4b.
 * Requirements 5.4, 5.5, 1.5. design.md §9.3, §9.4.
 *
 * The load-bearing claim is that the two axes are INDEPENDENT. §9.3 asks for four
 * distinguishable surfaces (Requirement 5.5) and, separately, for a provisional verdict
 * to stay visibly provisional (§9.3 point 4). A local error and a confirmed guidance note
 * must both be expressible, because the builder renders the first one on every unsaved
 * graph — and the live-region role must follow severity alone, since `role="alert"`
 * interrupts a screen reader and guidance has nothing to interrupt anyone about.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import {
  DEPLOYED_LOCK_FALLBACK_REASON,
  DeployedLockNotice,
  DestructiveConfirm,
  LOCAL_CHECK_NOTE,
  PROVENANCE,
  SURFACE,
  VALIDATION_PROVENANCES,
  VALIDATION_SURFACES,
  ValidationSurface,
  localCheckNote,
  provenanceRail,
  surfaceIcon,
  surfaceRole,
  surfaceSeverity,
  surfaceTreatment,
} from '../../../src/components/builder/validationSurfaces';
import { alertRole } from '../../../src/components/ds/Alert';
import { resetOverlayRegistry } from '../../../src/components/ds/overlayRegistry';
import { statusToken } from '../../../src/design/semantic';

/** The three surfaces that render as a band. `destructive` is a dialog. */
const BANNER_SURFACES = VALIDATION_SURFACES.filter((s) => s !== SURFACE.DESTRUCTIVE);

const band = () => document.querySelector('[data-ds="validation-surface"]');
const alert = () => document.querySelector('[data-ds="alert"]');
const dialog = () => document.querySelector('[role="dialog"]');

/** Development throws on a contract violation; the throw is the assertion. */
function silenceExpectedThrow() {
  const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
  return () => spy.mockRestore();
}

beforeEach(() => {
  resetOverlayRegistry();
  vi.stubEnv('DEV', true);
});

afterEach(() => {
  cleanup();
  resetOverlayRegistry();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

/* ══════════════════════════════════════════════════════════════════════════
 * Axis 1 — the four surfaces are distinct (Requirement 5.5, P10)
 * ══════════════════════════════════════════════════════════════════════════ */

describe('the severity axis: four surfaces, distinguishable on more than hue', () => {
  it('declares exactly §9.3s four rows', () => {
    expect(VALIDATION_SURFACES).toEqual(['guidance', 'warning', 'error', 'destructive']);
  });

  it('gives each surface an icon nothing else has', () => {
    // Three of the five hues in `tokens.css` are shared, so hue alone cannot separate
    // four surfaces. The glyph is the axis that can.
    const icons = VALIDATION_SURFACES.map(surfaceIcon);
    expect(new Set(icons).size).toBe(VALIDATION_SURFACES.length);
  });

  it('resolves each row to §9.3s token, border and role', () => {
    expect(surfaceTreatment(SURFACE.GUIDANCE)).toMatchObject({
      severity: 'guidance',
      tokenState: 'guidance',
      role: 'status',
      border: 'dashed',
    });
    expect(surfaceTreatment(SURFACE.WARNING)).toMatchObject({
      severity: 'warning',
      tokenState: 'warning',
      role: 'status',
      border: 'solid',
    });
    expect(surfaceTreatment(SURFACE.ERROR)).toMatchObject({
      severity: 'error',
      tokenState: 'error',
      role: 'alert',
      border: 'solid',
    });
    expect(surfaceTreatment(SURFACE.DESTRUCTIVE)).toMatchObject({
      severity: null,
      tokenState: 'error',
      role: 'dialog',
      border: 'solid',
    });
  });

  it('never gives guidance the assertive role', () => {
    // `role="alert"` interrupts whatever a screen reader is reading. An author mid-drag
    // has nothing wrong with their strategy, so cutting them off to say so is noise
    // delivered to the users least able to ignore it (Requirement 16.2).
    expect(surfaceRole(SURFACE.GUIDANCE)).toBe('status');
    expect(surfaceRole(SURFACE.WARNING)).toBe('status');
    expect(surfaceRole(SURFACE.ERROR)).toBe('alert');
    expect(surfaceRole(SURFACE.DESTRUCTIVE)).toBe('dialog');
  });

  it('cannot drift from ds/Alerts own severity table', () => {
    // The band surfaces delegate to `ds/Alert`, so this modules declared role has to be
    // the role the alert will actually render. Asserted rather than assumed: two tables
    // spelling §9.3 is the drift this module exists to prevent.
    BANNER_SURFACES.forEach((surface) => {
      expect(surfaceRole(surface)).toBe(alertRole(surfaceSeverity(surface)));
    });
  });

  it('is total, and an unrecognised surface is neither silent nor assertive', () => {
    ['', 'nope', null, undefined, 7, {}].forEach((value) => {
      expect(surfaceRole(value)).toBe('status');
      expect(surfaceSeverity(value)).toBe('warning');
    });
  });

  it('normalises case and whitespace rather than falling through', () => {
    expect(surfaceSeverity('  GUIDANCE ')).toBe('guidance');
    expect(surfaceRole('Error')).toBe('alert');
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * Axis 2 — provenance
 * ══════════════════════════════════════════════════════════════════════════ */

describe('the provenance axis: dashed for a local check, solid for the backends', () => {
  it('declares the two values', () => {
    expect(VALIDATION_PROVENANCES).toEqual(['local', 'confirmed']);
  });

  it('rails a local verdict dashed and a confirmed one solid', () => {
    expect(provenanceRail(PROVENANCE.LOCAL)).toBe('dashed');
    expect(provenanceRail(PROVENANCE.CONFIRMED)).toBe('solid');
  });

  it('understates authority when it cannot tell', () => {
    // Overstating presents a guess as the servers answer. Only one of the two
    // directions can cost a trader a deployment.
    ['', 'nope', null, undefined, 7, {}].forEach((value) => {
      expect(provenanceRail(value)).toBe('dashed');
    });
  });

  it('keeps the provisional sentence verbatim', () => {
    // The only thing telling an author a verdict is not authoritative. Reworded it stops
    // being that: "not yet validated" and "not validated" are different claims.
    expect(LOCAL_CHECK_NOTE).toBe(
      '(local check — the backend has not validated this version yet)',
    );
    expect(localCheckNote(PROVENANCE.LOCAL)).toBe(LOCAL_CHECK_NOTE);
    expect(localCheckNote(PROVENANCE.CONFIRMED)).toBe('');
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * The axes stay independent
 * ══════════════════════════════════════════════════════════════════════════ */

describe('the two axes are independent', () => {
  it('renders every surface × provenance pair', () => {
    // The cross-product is the point. A local `error` is the builders most common
    // banner and a confirmed `guidance` note is equally sayable; a single five-value
    // enum would make one of them unsayable.
    BANNER_SURFACES.forEach((surface) => {
      VALIDATION_PROVENANCES.forEach((provenance) => {
        const view = render(
          <ValidationSurface surface={surface} provenance={provenance} title="Condition" />,
        );
        expect(band().getAttribute('data-surface')).toBe(surface);
        expect(band().getAttribute('data-provenance')).toBe(provenance);
        view.unmount();
      });
    });
  });

  it('lets provenance change the rail without touching the role', () => {
    const view = render(
      <ValidationSurface surface={SURFACE.ERROR} provenance={PROVENANCE.LOCAL} title="3 errors" />,
    );
    expect(band().style.borderLeftStyle).toBe('dashed');
    expect(alert().getAttribute('role')).toBe('alert');

    view.rerender(
      <ValidationSurface
        surface={SURFACE.ERROR}
        provenance={PROVENANCE.CONFIRMED}
        title="3 errors"
      />,
    );
    expect(band().style.borderLeftStyle).toBe('solid');
    expect(alert().getAttribute('role')).toBe('alert');
  });

  it('lets severity change the role without touching the rail', () => {
    const view = render(
      <ValidationSurface surface={SURFACE.GUIDANCE} provenance={PROVENANCE.LOCAL} title="Not yet" />,
    );
    expect(alert().getAttribute('role')).toBe('status');
    expect(band().style.borderLeftStyle).toBe('dashed');

    view.rerender(
      <ValidationSurface surface={SURFACE.ERROR} provenance={PROVENANCE.LOCAL} title="Not yet" />,
    );
    expect(alert().getAttribute('role')).toBe('alert');
    expect(band().style.borderLeftStyle).toBe('dashed');
  });

  it('says the local-check sentence as well as drawing the rail', () => {
    // A rail alone is colour and shape: invisible to a screen reader and to anyone
    // reading a screenshot. One prop drives both channels.
    render(
      <ValidationSurface surface={SURFACE.ERROR} provenance={PROVENANCE.LOCAL} title="3 errors" />,
    );
    expect(screen.getByText(LOCAL_CHECK_NOTE)).toBeTruthy();
  });

  it('does not claim a backend verdict is unvalidated', () => {
    render(
      <ValidationSurface
        surface={SURFACE.ERROR}
        provenance={PROVENANCE.CONFIRMED}
        title="3 errors"
      />,
    );
    expect(screen.queryByText(LOCAL_CHECK_NOTE)).toBeNull();
  });

  it('requires provenance, because neither default is safe', () => {
    const restore = silenceExpectedThrow();
    expect(() => render(<ValidationSurface surface={SURFACE.ERROR} title="3 errors" />)).toThrow(
      /provenance/,
    );
    restore();
  });

  it('falls back to local in production rather than overstating authority', () => {
    cleanup();
    vi.stubEnv('DEV', false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<ValidationSurface surface={SURFACE.ERROR} title="3 errors" />);
    expect(band().getAttribute('data-provenance')).toBe('local');
    expect(band().style.borderLeftStyle).toBe('dashed');
    expect(screen.getByText(LOCAL_CHECK_NOTE)).toBeTruthy();
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * ValidationSurface composes ds/Alert rather than repainting a band
 * ══════════════════════════════════════════════════════════════════════════ */

describe('ValidationSurface: composed, and no colour of its own', () => {
  it('delegates hue, icon, border style and role to ds/Alert', () => {
    render(
      <ValidationSurface
        surface={SURFACE.GUIDANCE}
        provenance={PROVENANCE.LOCAL}
        title="Cannot connect: a DATA block has no input port"
      />,
    );
    expect(alert().getAttribute('data-alert-severity')).toBe('guidance');
    expect(alert().style.borderStyle).toBe('dashed');
  });

  it('takes the rail hue from the same token ds/Alert resolves', () => {
    // Asked of `statusToken` with the same state key, so the rail cannot end up a
    // different colour from the band it is attached to.
    render(
      <ValidationSurface surface={SURFACE.WARNING} provenance={PROVENANCE.CONFIRMED} title="Note" />,
    );
    expect(band().style.borderLeftColor).toBeTruthy();
    expect(alert().getAttribute('data-status-group')).toBe(
      statusToken(surfaceTreatment(SURFACE.WARNING).tokenState).group,
    );
  });

  it('forwards the call sites data attributes onto the announced element', () => {
    // The `data-testid` and the `role` land on one element, so an existing page test that
    // pairs them keeps working after the repoint.
    render(
      <ValidationSurface
        surface={SURFACE.WARNING}
        provenance={PROVENANCE.CONFIRMED}
        title="Live updates unavailable"
        data-testid="subscription-refusals"
        data-channel="strategy:42"
      />,
    );
    const node = screen.getByTestId('subscription-refusals');
    expect(node.getAttribute('data-channel')).toBe('strategy:42');
    expect(node.getAttribute('role')).toBe('status');
  });

  it('renders full-width strips by default and blocks on request', () => {
    const view = render(
      <ValidationSurface surface={SURFACE.WARNING} provenance={PROVENANCE.LOCAL} title="Note" />,
    );
    expect(alert().getAttribute('data-alert-variant')).toBe('strip');
    view.rerender(
      <ValidationSurface
        surface={SURFACE.WARNING}
        provenance={PROVENANCE.LOCAL}
        title="Note"
        variant="block"
      />,
    );
    expect(alert().getAttribute('data-alert-variant')).toBe('block');
  });

  it('refuses to render a destructive action as a band', () => {
    // Requirement 5.5 is about distinctness. A delete that looks like a notice is the
    // failure, so this is refused rather than quietly downgraded to `error`.
    const restore = silenceExpectedThrow();
    expect(() =>
      render(
        <ValidationSurface
          surface={SURFACE.DESTRUCTIVE}
          provenance={PROVENANCE.CONFIRMED}
          title="Delete"
        />,
      ),
    ).toThrow(/DestructiveConfirm/);
    restore();

    cleanup();
    vi.stubEnv('DEV', false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <ValidationSurface
        surface={SURFACE.DESTRUCTIVE}
        provenance={PROVENANCE.CONFIRMED}
        title="Delete"
      />,
    );
    expect(band()).toBeNull();
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * The deployed lock (§9.4)
 * ══════════════════════════════════════════════════════════════════════════ */

describe('DeployedLockNotice: the warning surface, and a real Lock', () => {
  it('renders the servers sentence on the confirmed warning surface', () => {
    render(
      <DeployedLockNotice
        reason="Version 4 is deployed on Binance, so the canvas is read-only."
        data-testid="deployed-lock"
      />,
    );
    expect(band().getAttribute('data-surface')).toBe('warning');
    expect(band().getAttribute('data-provenance')).toBe('confirmed');
    // Solid: the verdict, the sentence and the frozen fields are all `canvas_state`.
    expect(band().style.borderLeftStyle).toBe('solid');
    expect(
      screen.getByText('Version 4 is deployed on Binance, so the canvas is read-only.'),
    ).toBeTruthy();
    // Not guidance: nothing the author is part-way through caused it and nothing they
    // can do clears it, so it never says the backend has not looked.
    expect(screen.queryByText(LOCAL_CHECK_NOTE)).toBeNull();
  });

  it('falls back to the sentence the banner already carries', () => {
    render(<DeployedLockNotice />);
    expect(screen.getByText(DEPLOYED_LOCK_FALLBACK_REASON)).toBeTruthy();
  });

  it('carries a Lock glyph and no emoji', () => {
    const { container } = render(<DeployedLockNotice />);
    const glyph = container.querySelector('[data-ds="validation-surface-glyph"]');
    expect(glyph).toBeTruthy();
    // `aria-hidden`, as the emoji was: the sentence carries the meaning, and an
    // announced glyph would prefix the notice with its own name.
    expect(glyph.getAttribute('aria-hidden')).toBe('true');
    expect(container.textContent).not.toContain('🔒');
  });

  it('keeps ds/Alerts severity glyph as well as the subject glyph', () => {
    // Two glyphs saying two different things. Overriding the severity icon would let two
    // surfaces come to share one, which is the axis that separates them.
    const { container } = render(<DeployedLockNotice />);
    expect(container.querySelectorAll('svg').length).toBeGreaterThan(1);
  });

  it('makes the frozen fields readable instead of only a data attribute', () => {
    render(<DeployedLockNotice frozenFields={['symbol', 'timeframe']} />);
    expect(screen.getByText('Frozen fields: symbol, timeframe')).toBeTruthy();
    expect(band().querySelector('[data-frozen-fields="symbol timeframe"]')).toBeTruthy();
  });

  it('says nothing about frozen fields when the server names none', () => {
    const { container } = render(<DeployedLockNotice frozenFields={[]} />);
    expect(container.textContent).not.toContain('Frozen fields');
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * The destructive surface
 * ══════════════════════════════════════════════════════════════════════════ */

describe('DestructiveConfirm: a dialog, at status.error, with no checkbox', () => {
  const BASE = Object.freeze({
    open: true,
    onCancel: () => {},
    onConfirm: () => {},
    title: 'Delete this strategy?',
  });

  it('renders ds/ConfirmDialog at the destructive intent', () => {
    render(<DestructiveConfirm {...BASE} statement="This cannot be undone." />);
    expect(dialog().getAttribute('data-ds-intent')).toBe('destructive');
    expect(screen.getByText('This cannot be undone.')).toBeTruthy();
  });

  it('carries §9.3s Trash2 at the head of the body', () => {
    render(<DestructiveConfirm {...BASE} statement="This cannot be undone." />);
    const statement = dialog().querySelector('[data-ds="destructive-statement"]');
    expect(statement).toBeTruthy();
    expect(statement.querySelector('svg')).toBeTruthy();
  });

  it('opens with focus on cancel, so Enter out of habit lands on no', () => {
    render(<DestructiveConfirm {...BASE} statement="This cannot be undone." />);
    expect(document.activeElement.textContent).toBe('Cancel');
  });

  it('names the act rather than saying OK', () => {
    render(<DestructiveConfirm {...BASE} />);
    expect(screen.getByRole('button', { name: 'Delete' })).toBeTruthy();
  });

  it('spends no acknowledgement checkbox, and refuses one', () => {
    // A gate on everything is a gate that gets ticked unread, and the one place that must
    // never happen is the live deployment dialog.
    const restore = silenceExpectedThrow();
    expect(() =>
      render(
        <DestructiveConfirm
          {...BASE}
          acknowledgement={{ statement: 'I understand', label: 'I understand' }}
        />,
      ),
    ).toThrow(/acknowledgement/);
    restore();

    cleanup();
    resetOverlayRegistry();
    vi.stubEnv('DEV', false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <DestructiveConfirm
        {...BASE}
        acknowledgement={{ statement: 'I understand', label: 'I understand' }}
      />,
    );
    expect(screen.queryByRole('checkbox')).toBeNull();
    // The dialog still renders and can still be cancelled: refusing to paint it would be
    // worse than painting it without a gate it should never have been asked for.
    expect(dialog()).toBeTruthy();
  });

  it('confirms on an explicit activation', () => {
    let confirms = 0;
    render(
      <DestructiveConfirm
        {...BASE}
        onConfirm={() => {
          confirms += 1;
        }}
      />,
    );
    screen.getByRole('button', { name: 'Delete' }).click();
    expect(confirms).toBe(1);
  });

  it('passes the review grid through, and never defaults a missing value', () => {
    render(
      <DestructiveConfirm
        {...BASE}
        review={[
          { label: 'Strategy', value: 'RSI Reversion' },
          { label: 'Deployments', value: null },
        ]}
      />,
    );
    expect(screen.getByText('RSI Reversion')).toBeTruthy();
    expect(screen.getByLabelText('Deployments: not available')).toBeTruthy();
  });

  it('fixes the intent, so a destructive action cannot be confirmed calmly', () => {
    const restore = silenceExpectedThrow();
    expect(() => render(<DestructiveConfirm {...BASE} intent="neutral" />)).toThrow(/intent/);
    restore();
  });
});
