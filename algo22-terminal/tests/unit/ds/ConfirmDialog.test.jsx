/**
 * `ds/ConfirmDialog` — vyomquant-ui-redesign task 6.20.
 * Requirements 7.6, 8.1, 8.2, 8.3, 8.5, 17.3, 18.1, 18.3, 18.4. design.md §5.1, §8.3, §8.4.
 *
 * The four safety decisions this component's header states, asserted:
 *
 *   1. initial focus is on CANCEL
 *   2. the acknowledgement gates confirm in two independent places
 *   3. a missing review value renders the not-available marker, never `0` and never nothing
 *   4. a malformed acknowledgement fails closed
 *
 * Properties P34 (viewport / single overlay) and P35 (focus trap) are tasks 6.21 and 6.22.
 */

import { useRef, useState } from 'react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup, screen, act, fireEvent } from '@testing-library/react';

import ConfirmDialog from '../../../src/components/ds/ConfirmDialog';
import Drawer from '../../../src/components/ds/Drawer';
import { isOverlayOpen, resetOverlayRegistry } from '../../../src/components/ds/overlayRegistry';

const BASE = Object.freeze({
  open: true,
  title: 'Stop live deployment',
  onCancel: () => {},
  onConfirm: () => {},
});

beforeEach(() => {
  resetOverlayRegistry();
  vi.stubEnv('DEV', true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  resetOverlayRegistry();
});

const dialog = () => screen.getByRole('dialog');
const backdrop = () => document.body.querySelector('[data-ds="confirm-dialog-backdrop"]');
const cancelButton = () => screen.getByRole('button', { name: 'Keep running' });
const confirmButton = () => screen.getByRole('button', { name: 'Stop deployment' });

/**
 * A development contract violation throws from inside a React commit, so React re-reports it
 * through `window.onerror` and jsdom copies it to stderr. Both are silenced for the cases
 * that *assert* the throw, so a passing run does not read like a failing one.
 *
 * @returns {Function} teardown
 */
function silenceExpectedThrow() {
  const swallow = (event) => event.preventDefault();
  window.addEventListener('error', swallow);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  return () => window.removeEventListener('error', swallow);
}

const LABELS = Object.freeze({ confirmLabel: 'Stop deployment', cancelLabel: 'Keep running' });

describe('ConfirmDialog: open and closed', () => {
  it('renders nothing when closed, and claims no overlay', () => {
    render(<ConfirmDialog {...BASE} open={false} />);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(isOverlayOpen()).toBe(false);
  });

  it('renders through a portal on document.body, so no ancestor overflow can clip it', () => {
    const view = render(
      <div style={{ overflow: 'hidden' }}>
        <ConfirmDialog {...BASE} {...LABELS} />
      </div>,
    );
    expect(view.container.querySelector('[role="dialog"]')).toBeNull();
    expect(dialog().closest('body')).toBe(document.body);
  });

  it('releases its overlay claim on unmount', () => {
    const view = render(<ConfirmDialog {...BASE} {...LABELS} />);
    expect(isOverlayOpen()).toBe(true);
    view.unmount();
    expect(isOverlayOpen()).toBe(false);
  });

  it('releases its overlay claim when `open` goes false', () => {
    const view = render(<ConfirmDialog {...BASE} {...LABELS} />);
    expect(isOverlayOpen()).toBe(true);
    view.rerender(<ConfirmDialog {...BASE} {...LABELS} open={false} />);
    expect(isOverlayOpen()).toBe(false);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('ConfirmDialog: ARIA (Requirements 18.3, 18.4)', () => {
  it('is a modal dialog named by its title', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} />);
    const node = dialog();
    expect(node.getAttribute('aria-modal')).toBe('true');
    const titleId = node.getAttribute('aria-labelledby');
    expect(titleId).toBeTruthy();
    expect(document.getElementById(titleId).textContent).toBe('Stop live deployment');
    // `getByRole` with a name is the accessible-name computation; this is the assertion
    // that a screen reader announces more than "dialog".
    expect(screen.getByRole('dialog', { name: 'Stop live deployment' })).toBe(node);
  });

  it('describes itself by its body when it has one', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} description="The open position stays open." />);
    const describedBy = dialog().getAttribute('aria-describedby');
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy).textContent).toContain('The open position stays open.');
  });

  it('omits aria-describedby rather than pointing at an empty element', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} />);
    expect(dialog().hasAttribute('aria-describedby')).toBe(false);
  });
});

describe('ConfirmDialog: focus (safety decision 1)', () => {
  it('puts initial focus on CANCEL, not confirm', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} />);
    expect(document.activeElement).toBe(cancelButton());
  });

  it('puts cancel before confirm in the DOM, so tab order matches visual order', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} />);
    const order = [...dialog().querySelectorAll('button')].map((b) => b.textContent);
    expect(order).toEqual(['Keep running', 'Stop deployment']);
  });

  it('traps Tab inside the dialog', () => {
    render(
      <ConfirmDialog {...BASE} {...LABELS} review={[{ label: 'Strategy', value: 'RSI Reversion' }]} />,
    );
    const node = dialog();
    for (let index = 0; index < 8; index += 1) {
      fireEvent.keyDown(document.activeElement, { key: 'Tab', shiftKey: index % 3 === 0 });
      expect(node.contains(document.activeElement)).toBe(true);
    }
  });

  it('returns focus to the trigger on close', () => {
    function Host() {
      const [open, setOpen] = useState(false);
      const trigger = useRef(null);
      return (
        <>
          <button type="button" ref={trigger} onClick={() => setOpen(true)}>
            Stop
          </button>
          <ConfirmDialog
            {...LABELS}
            open={open}
            title="Stop live deployment"
            onCancel={() => setOpen(false)}
            onConfirm={() => {}}
          />
        </>
      );
    }
    render(<Host />);
    const trigger = screen.getByRole('button', { name: 'Stop' });
    act(() => {
      trigger.focus();
      trigger.click();
    });
    expect(document.activeElement).toBe(cancelButton());

    act(() => {
      cancelButton().click();
    });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});

describe('ConfirmDialog: cancelling', () => {
  it('cancels on the cancel action', () => {
    let cancels = 0;
    render(<ConfirmDialog {...BASE} {...LABELS} onCancel={() => { cancels += 1; }} />);
    act(() => {
      cancelButton().click();
    });
    expect(cancels).toBe(1);
  });

  it('cancels on Escape', () => {
    let cancels = 0;
    render(<ConfirmDialog {...BASE} {...LABELS} onCancel={() => { cancels += 1; }} />);
    fireEvent.keyDown(document.activeElement, { key: 'Escape' });
    expect(cancels).toBe(1);
  });
});

describe('ConfirmDialog: the review grid (Requirement 8.1, safety decision 3)', () => {
  const REVIEW = Object.freeze([
    { label: 'Strategy', value: 'RSI Reversion' },
    { label: 'Version', value: 'v7' },
    { label: 'Exchange', value: 'Binance' },
    { label: 'Estimated exposure', value: null },
    { label: 'Reduce only', value: false },
    { label: 'Leverage', value: 0 },
    { label: 'Account', value: '   ' },
  ]);

  it('renders every supplied row', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} review={REVIEW} />);
    for (const { label } of REVIEW) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });

  it('renders the not-available marker for a field the caller cannot supply', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} review={REVIEW} />);
    // Named, not merely blank: a screen reader hears "Estimated exposure: not available".
    const marker = screen.getByLabelText('Estimated exposure: not available');
    expect(marker.textContent).toBe('—');
    expect(screen.getByLabelText('Account: not available')).toBeTruthy();
  });

  it('does not omit or default a missing field', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} review={REVIEW} />);
    const rows = dialog().querySelectorAll('dt');
    expect(rows).toHaveLength(REVIEW.length);
    // The absence is never rendered as a zero.
    expect(screen.getByLabelText('Estimated exposure: not available').textContent).not.toBe('0');
  });

  it('treats 0 and false as readings, not absences (Requirement 14.5)', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} review={REVIEW} />);
    expect(screen.queryByLabelText('Leverage: not available')).toBeNull();
    expect(screen.queryByLabelText('Reduce only: not available')).toBeNull();
    expect(screen.getByText('0')).toBeTruthy();
  });

  it('renders no grid at all when no review is supplied', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} />);
    expect(dialog().querySelector('dl')).toBeNull();
  });

  it('throws in development on a row with no label, and still renders in production', () => {
    const restore = silenceExpectedThrow();
    expect(() =>
      render(<ConfirmDialog {...BASE} {...LABELS} review={[{ value: 'orphan' }]} />),
    ).toThrow(/review/);
    restore();

    cleanup();
    resetOverlayRegistry();
    vi.stubEnv('DEV', false);
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <ConfirmDialog
        {...BASE}
        {...LABELS}
        review={[{ value: 'orphan' }, { label: 'Strategy', value: 'RSI Reversion' }]}
      />,
    );
    // The dialog still paints — refusing to render a half-configured review would be worse
    // than rendering it with the unlabelled row dropped.
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText('RSI Reversion')).toBeTruthy();
    expect(screen.queryByText('orphan')).toBeNull();
    expect(logged).toHaveBeenCalled();
  });
});

describe('ConfirmDialog: the acknowledgement gate (Requirements 8.2, 8.3)', () => {
  const ACK = Object.freeze({
    statement: 'Confirming will place real orders on Binance using real funds in account …4821.',
    control: 'checkbox',
    label: 'I understand this places real orders with real funds',
  });

  it('renders the real-funds statement and its control', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} intent="live" environment="LIVE" acknowledgement={ACK} />);
    expect(screen.getByText(ACK.statement)).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: ACK.label })).toBeTruthy();
  });

  it('disables confirm until the acknowledgement is satisfied', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} acknowledgement={ACK} />);
    expect(confirmButton().disabled).toBe(true);

    act(() => {
      fireEvent.click(screen.getByRole('checkbox'));
    });
    expect(confirmButton().disabled).toBe(false);
  });

  it('does not call onConfirm while the acknowledgement is unsatisfied, even on a direct click', () => {
    // Safety decision 2: `disabled` is a rendering, the guard inside the handler is the rule.
    let confirms = 0;
    render(
      <ConfirmDialog {...BASE} {...LABELS} acknowledgement={ACK} onConfirm={() => { confirms += 1; }} />,
    );
    act(() => {
      confirmButton().click();
      fireEvent.click(confirmButton());
    });
    expect(confirms).toBe(0);
  });

  it('calls onConfirm once the acknowledgement is satisfied', () => {
    let confirms = 0;
    render(
      <ConfirmDialog {...BASE} {...LABELS} acknowledgement={ACK} onConfirm={() => { confirms += 1; }} />,
    );
    act(() => {
      fireEvent.click(screen.getByRole('checkbox'));
    });
    act(() => {
      confirmButton().click();
    });
    expect(confirms).toBe(1);
  });

  it('forgets the acknowledgement when the dialog is reopened', () => {
    // A dialog that remembered it is a dialog that can place a live order without an
    // acknowledgement in this session.
    const view = render(<ConfirmDialog {...BASE} {...LABELS} acknowledgement={ACK} />);
    act(() => {
      fireEvent.click(screen.getByRole('checkbox'));
    });
    expect(confirmButton().disabled).toBe(false);

    view.rerender(<ConfirmDialog {...BASE} {...LABELS} acknowledgement={ACK} open={false} />);
    view.rerender(<ConfirmDialog {...BASE} {...LABELS} acknowledgement={ACK} />);

    expect(screen.getByRole('checkbox').checked).toBe(false);
    expect(confirmButton().disabled).toBe(true);
  });

  it('forgets the acknowledgement when the statement changes', () => {
    const view = render(<ConfirmDialog {...BASE} {...LABELS} acknowledgement={ACK} />);
    act(() => {
      fireEvent.click(screen.getByRole('checkbox'));
    });
    view.rerender(
      <ConfirmDialog
        {...BASE}
        {...LABELS}
        acknowledgement={{ ...ACK, statement: 'Confirming will place real orders in account …9999.' }}
      />,
    );
    expect(confirmButton().disabled).toBe(true);
  });

  it('keeps the acknowledgement across an unrelated re-render with an inline object', () => {
    // The reset is keyed on the statement STRING, not the object identity, so a caller
    // passing a literal does not clear the gate on every render.
    const view = render(<ConfirmDialog {...BASE} {...LABELS} acknowledgement={{ ...ACK }} />);
    act(() => {
      fireEvent.click(screen.getByRole('checkbox'));
    });
    view.rerender(<ConfirmDialog {...BASE} {...LABELS} acknowledgement={{ ...ACK }} />);
    expect(confirmButton().disabled).toBe(false);
  });

  it('confirms immediately when no acknowledgement is asked for (Requirement 8.4)', () => {
    let confirms = 0;
    render(<ConfirmDialog {...BASE} {...LABELS} onConfirm={() => { confirms += 1; }} />);
    expect(confirmButton().disabled).toBe(false);
    expect(screen.queryByRole('checkbox')).toBeNull();
    act(() => {
      confirmButton().click();
    });
    expect(confirms).toBe(1);
  });

  it('never constructs the real-funds statement on the paper path', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} environment="PAPER" />);
    expect(screen.queryByText(/real funds/i)).toBeNull();
  });
});

describe('ConfirmDialog: a malformed acknowledgement fails closed (safety decision 4)', () => {
  it('throws in development when the statement is missing', () => {
    const restore = silenceExpectedThrow();
    expect(() =>
      render(<ConfirmDialog {...BASE} {...LABELS} acknowledgement={{ label: 'I understand' }} />),
    ).toThrow(/statement/);
    restore();
  });

  it('keeps the gate on in production and invents no statement', () => {
    vi.stubEnv('DEV', false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<ConfirmDialog {...BASE} {...LABELS} acknowledgement={{}} />);

    // The gate is still there, and its control still has a name.
    expect(confirmButton().disabled).toBe(true);
    expect(screen.getByRole('checkbox', { name: 'I understand and accept this action' })).toBeTruthy();
    // No claim about funds or orders is fabricated.
    expect(screen.queryByText(/real funds/i)).toBeNull();
  });

  it('renders a checkbox for an unsupported control rather than dropping the gate', () => {
    vi.stubEnv('DEV', false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <ConfirmDialog
        {...BASE}
        {...LABELS}
        acknowledgement={{ statement: 'Real funds.', label: 'I understand', control: 'type-to-confirm' }}
      />,
    );
    expect(screen.getByRole('checkbox', { name: 'I understand' })).toBeTruthy();
    expect(confirmButton().disabled).toBe(true);
  });
});

describe('ConfirmDialog: busy', () => {
  const ACK = Object.freeze({ statement: 'Real funds.', label: 'I understand' });

  it('disables both actions while a request is in flight', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} busy busyLabel="Stopping…" />);
    expect(cancelButton().disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Stopping…' }).disabled).toBe(true);
  });

  it('refuses Escape while busy — a dialog cannot un-send a POST', () => {
    let cancels = 0;
    render(<ConfirmDialog {...BASE} {...LABELS} busy onCancel={() => { cancels += 1; }} />);
    fireEvent.keyDown(document.activeElement, { key: 'Escape' });
    expect(cancels).toBe(0);
  });

  it('does not call onConfirm while busy, even with the acknowledgement satisfied', () => {
    let confirms = 0;
    const view = render(
      <ConfirmDialog {...BASE} {...LABELS} acknowledgement={ACK} onConfirm={() => { confirms += 1; }} />,
    );
    act(() => {
      fireEvent.click(screen.getByRole('checkbox'));
    });
    view.rerender(
      <ConfirmDialog
        {...BASE}
        {...LABELS}
        busy
        busyLabel="Stopping…"
        acknowledgement={ACK}
        onConfirm={() => { confirms += 1; }}
      />,
    );
    act(() => {
      screen.getByRole('button', { name: 'Stopping…' }).click();
    });
    expect(confirms).toBe(0);
  });

  it('keeps focus inside even when every control is disabled', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} busy busyLabel="Stopping…" />);
    const node = dialog();
    expect(node.contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(document.activeElement, { key: 'Tab' });
    expect(node.contains(document.activeElement)).toBe(true);
  });
});

describe('ConfirmDialog: the environment badge (Requirement 8.5)', () => {
  it('renders the LIVE treatment', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} environment="LIVE" />);
    expect(screen.getByText('LIVE')).toBeTruthy();
    expect(screen.getByText('Live — real funds, real orders')).toBeTruthy();
  });

  it('renders the PAPER treatment, which differs in label and long form as well as hue', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} environment="PAPER" />);
    expect(screen.getByText('PAPER TRADING')).toBeTruthy();
    expect(screen.getByText('Simulated — no live order is ever placed')).toBeTruthy();
  });

  it('renders "unconfirmed" for an unrecognised environment rather than guessing one', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} environment="probably-live" />);
    expect(screen.getByText('ENVIRONMENT UNCONFIRMED')).toBeTruthy();
    expect(screen.queryByText('LIVE')).toBeNull();
  });

  it('renders no badge for a confirmation that is not about trading', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} />);
    expect(dialog().querySelector('[data-ds="environment-strip"]')).toBeNull();
  });
});

describe('ConfirmDialog: the error state (Requirements 14.3, 14.4)', () => {
  it('renders translated copy for a backend error code', () => {
    render(
      <ConfirmDialog {...BASE} {...LABELS} error={{ data: { error: 'STRATEGY_DEPLOY_FAILED' } }} />,
    );
    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.getByText('Deployment did not start')).toBeTruthy();
  });

  it('never renders a raw exception message', () => {
    const raw = new Error('TypeError: cannot read property x of undefined at deploy.js:42');
    render(<ConfirmDialog {...BASE} {...LABELS} error={raw} />);
    expect(dialog().textContent).not.toContain('TypeError');
    expect(dialog().textContent).not.toContain('deploy.js');
    expect(screen.getByRole('alert')).toBeTruthy();
  });

  it('renders no retry control of its own — the confirm action is the retry', () => {
    render(
      <ConfirmDialog {...BASE} {...LABELS} error={{ data: { error: 'STRATEGY_DEPLOY_FAILED' } }} />,
    );
    const names = [...dialog().querySelectorAll('button')].map((b) => b.textContent);
    expect(names).toEqual(['Keep running', 'Stop deployment']);
  });

  it('renders nothing extra when there is no error', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} />);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('ConfirmDialog: intent and the viewport clamp', () => {
  it.each([
    ['destructive', 'destructive'],
    ['live', 'live'],
    ['neutral', 'neutral'],
  ])('records intent %s on the dialog', (intent, expected) => {
    render(<ConfirmDialog {...BASE} {...LABELS} intent={intent} />);
    expect(dialog().dataset.dsIntent).toBe(expected);
  });

  it('throws in development on an unknown intent', () => {
    const restore = silenceExpectedThrow();
    expect(() => render(<ConfirmDialog {...BASE} {...LABELS} intent="scary" />)).toThrow(/intent/);
    restore();
  });

  it('clamps to the viewport in the viewport\'s own units (Requirement 17.3)', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} />);
    const { style } = dialog();
    // Stated in dvh/vw, so there is no width at which the clamp fails to apply and no
    // breakpoint it depends on being listed.
    expect(style.maxHeight).toContain('100dvh');
    expect(style.maxHeight).toContain('--spacing-8');
    expect(style.maxWidth).toContain('560px');
    expect(style.maxWidth).toContain('100vw');
    // The stacking level sits on the backdrop, which is the portal's root element.
    expect(backdrop().style.zIndex).toBe('var(--z-modal)');
    expect(backdrop().style.position).toBe('fixed');
  });

  it('puts the only scroll region inside the dialog, not on the dialog', () => {
    render(<ConfirmDialog {...BASE} {...LABELS} description="Body copy." />);
    const node = dialog();
    expect(node.style.overflow).toBe('hidden');
    const body = node.querySelector('[data-ds="confirm-dialog-body"]');
    expect(body.style.overflowY).toBe('auto');
    // Without `min-height: 0` a flex child grows past its parent and takes the dialog
    // outside its own clamp.
    expect(body.style.minHeight).toBe('0');
  });
});

describe('ConfirmDialog: one overlay at a time (Requirement 17.3)', () => {
  it('throws in development when a drawer is already open', () => {
    const restore = silenceExpectedThrow();
    expect(() =>
      render(
        <>
          <Drawer open onClose={() => {}} title="Account" />
          <ConfirmDialog {...BASE} {...LABELS} />
        </>,
      ),
    ).toThrow(/Requirement 17.3/);
    restore();
  });

  it('is a no-op in production: the first overlay keeps the screen', () => {
    vi.stubEnv('DEV', false);
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <>
        <Drawer open onClose={() => {}} title="Account" />
        <ConfirmDialog {...BASE} {...LABELS} />
      </>,
    );
    const dialogs = screen.getAllByRole('dialog');
    expect(dialogs).toHaveLength(1);
    expect(dialogs[0].dataset.dsOverlay).toBe('drawer');
    expect(logged).toHaveBeenCalled();
  });

  it('lets the second overlay open once the first has closed', () => {
    function Host() {
      const [which, setWhich] = useState('drawer');
      return (
        <>
          <Drawer open={which === 'drawer'} onClose={() => {}} title="Account" />
          <ConfirmDialog {...BASE} {...LABELS} open={which === 'dialog'} />
          <button type="button" data-testid="swap" onClick={() => setWhich('dialog')}>
            swap
          </button>
        </>
      );
    }
    render(<Host />);
    expect(screen.getByRole('dialog').dataset.dsOverlay).toBe('drawer');
    act(() => {
      screen.getByTestId('swap').click();
    });
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(screen.getByRole('dialog').dataset.dsOverlay).toBe('dialog');
  });
});
