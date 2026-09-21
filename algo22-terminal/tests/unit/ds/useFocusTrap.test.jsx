/**
 * `useFocusTrap` — vyomquant-ui-redesign task 6.20.
 * Requirements 18.1, 18.2, 18.3. design.md §11.7.
 *
 * Task 6.22 owns Property 35, which generates Tab/Shift+Tab sequences of arbitrary length.
 * This file pins the four awkward cases that make such a property total rather than
 * probable, because a generator is unlikely to construct them and each one is a way focus
 * genuinely escapes a hand-rolled trap:
 *
 *   1. zero focusable descendants
 *   2. exactly one focusable descendant
 *   3. the focused element becoming disabled while the overlay is open
 *   4. the overlay's content changing while it is open
 *
 * Plus the two things that are not about Tab at all: initial focus on the *cancel* action,
 * and focus returning to the opener on close.
 */

import { useRef, useState } from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup, screen, act, fireEvent } from '@testing-library/react';

import { collectFocusable, useFocusTrap } from '../../../src/hooks/useFocusTrap';

afterEach(cleanup);

/** Tab / Shift+Tab delivered where the trap listens: `document`, in capture. */
function tab({ shift = false } = {}) {
  fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Tab', shiftKey: shift });
}

function escape() {
  fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
}

/**
 * A minimal overlay: the trap, a container with `tabIndex={-1}`, and whatever the case needs.
 * Deliberately not `ConfirmDialog` — this suite is about the hook.
 */
function Overlay({
  active = true,
  onEscape,
  children,
  withInitialFocus = false,
  hideBackground = true,
}) {
  const containerRef = useRef(null);
  const initialRef = useRef(null);
  useFocusTrap({
    active,
    containerRef,
    initialFocusRef: withInitialFocus ? initialRef : undefined,
    onEscape,
    hideBackground,
  });
  return (
    <div ref={containerRef} tabIndex={-1} data-testid="overlay">
      {withInitialFocus ? (
        <button type="button" ref={initialRef}>
          Cancel
        </button>
      ) : null}
      {children}
    </div>
  );
}

describe('collectFocusable', () => {
  it('returns nothing for a missing or non-element container', () => {
    expect(collectFocusable(null)).toEqual([]);
    expect(collectFocusable(undefined)).toEqual([]);
    expect(collectFocusable({})).toEqual([]);
  });

  it('collects the usual controls in DOM order', () => {
    const host = document.createElement('div');
    host.innerHTML = `
      <a href="/x">link</a>
      <button type="button">button</button>
      <input />
      <select><option>a</option></select>
      <textarea></textarea>
      <div tabindex="0">focusable div</div>
    `;
    document.body.appendChild(host);
    expect(collectFocusable(host).map((el) => el.tagName)).toEqual([
      'A',
      'BUTTON',
      'INPUT',
      'SELECT',
      'TEXTAREA',
      'DIV',
    ]);
    host.remove();
  });

  it('excludes disabled, aria-disabled, hidden, negatively-tabbable and display:none controls', () => {
    const host = document.createElement('div');
    host.innerHTML = `
      <button type="button" id="keep">keep</button>
      <button type="button" disabled>disabled</button>
      <button type="button" aria-disabled="true">aria-disabled</button>
      <button type="button" hidden>hidden</button>
      <button type="button" tabindex="-1">not a tab stop</button>
      <button type="button" style="display:none">display none</button>
      <button type="button" style="visibility:hidden">visibility hidden</button>
      <div style="display:none"><button type="button">inside a hidden parent</button></div>
      <div aria-hidden="true"><button type="button">inside an aria-hidden parent</button></div>
      <a>anchor with no href</a>
    `;
    document.body.appendChild(host);
    expect(collectFocusable(host).map((el) => el.id)).toEqual(['keep']);
    host.remove();
  });

  it('orders positive tabindex before the natural order, as the browser does', () => {
    const host = document.createElement('div');
    host.innerHTML = `
      <button type="button" id="natural-1">a</button>
      <button type="button" id="explicit-2" tabindex="2">b</button>
      <button type="button" id="natural-2">c</button>
      <button type="button" id="explicit-1" tabindex="1">d</button>
    `;
    document.body.appendChild(host);
    expect(collectFocusable(host).map((el) => el.id)).toEqual([
      'explicit-1',
      'explicit-2',
      'natural-1',
      'natural-2',
    ]);
    host.remove();
  });

  it('treats a checked radio group as one tab stop and an untouched one as its first member', () => {
    const host = document.createElement('div');
    host.innerHTML = `
      <input type="radio" name="env" id="live" />
      <input type="radio" name="env" id="paper" />
      <input type="radio" name="other" id="other" />
    `;
    document.body.appendChild(host);
    expect(collectFocusable(host).map((el) => el.id)).toEqual(['live', 'paper', 'other']);

    host.querySelector('#paper').checked = true;
    expect(collectFocusable(host).map((el) => el.id)).toEqual(['paper', 'other']);
    host.remove();
  });
});

describe('useFocusTrap: initial focus', () => {
  it('focuses the nominated element — cancel, not confirm', () => {
    render(
      <Overlay withInitialFocus>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }));
  });

  it('falls back to the first focusable when nothing is nominated', () => {
    render(
      <Overlay>
        <button type="button">First</button>
        <button type="button">Second</button>
      </Overlay>,
    );
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'First' }));
  });

  it('falls back to the container when there is nothing focusable at all', () => {
    render(
      <Overlay>
        <p>Nothing to focus here.</p>
      </Overlay>,
    );
    expect(document.activeElement).toBe(screen.getByTestId('overlay'));
  });

  it('does nothing while inactive', () => {
    render(
      <Overlay active={false} withInitialFocus>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    expect(document.activeElement).toBe(document.body);
  });
});

describe('useFocusTrap: the Tab cycle', () => {
  it('cycles forward and wraps', () => {
    render(
      <Overlay withInitialFocus>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    const confirm = screen.getByRole('button', { name: 'Confirm' });

    expect(document.activeElement).toBe(cancel);
    tab();
    expect(document.activeElement).toBe(confirm);
    tab();
    expect(document.activeElement).toBe(cancel);
  });

  it('cycles backward and wraps', () => {
    render(
      <Overlay withInitialFocus>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    const confirm = screen.getByRole('button', { name: 'Confirm' });

    expect(document.activeElement).toBe(cancel);
    tab({ shift: true });
    expect(document.activeElement).toBe(confirm);
    tab({ shift: true });
    expect(document.activeElement).toBe(cancel);
  });

  it('never leaves the container over a long mixed sequence', () => {
    render(
      <Overlay withInitialFocus>
        <input aria-label="Quantity" />
        <a href="/docs">Docs</a>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    const overlay = screen.getByTestId('overlay');
    const sequence = [false, false, true, false, true, true, false, true, false, false, true];

    for (const shift of sequence) {
      tab({ shift });
      expect(overlay.contains(document.activeElement)).toBe(true);
    }
  });

  it('consumes Tab so the browser cannot move focus itself', () => {
    render(
      <Overlay withInitialFocus>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    // `fireEvent` reports whether the event's default action survived.
    const notCancelled = fireEvent.keyDown(document.activeElement, { key: 'Tab' });
    expect(notCancelled).toBe(false);
  });

  it('keeps focus on the single focusable element when there is only one', () => {
    render(
      <Overlay>
        <button type="button">Only</button>
      </Overlay>,
    );
    const only = screen.getByRole('button', { name: 'Only' });
    expect(document.activeElement).toBe(only);
    tab();
    expect(document.activeElement).toBe(only);
    tab({ shift: true });
    expect(document.activeElement).toBe(only);
  });

  it('keeps focus on the container when there is nothing focusable', () => {
    render(
      <Overlay>
        <p>Working…</p>
      </Overlay>,
    );
    const overlay = screen.getByTestId('overlay');
    expect(document.activeElement).toBe(overlay);
    tab();
    expect(document.activeElement).toBe(overlay);
    tab({ shift: true });
    expect(document.activeElement).toBe(overlay);
  });

  it('re-enters the cycle when focus has fallen outside', () => {
    render(
      <Overlay withInitialFocus>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    const overlay = screen.getByTestId('overlay');

    // Simulate focus having been lost to the document without going through `focusin`
    // (a browser dropping focus after the active element vanished).
    act(() => {
      document.activeElement.blur();
    });
    tab();
    expect(overlay.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }));
  });

  it('re-enters from the far end on Shift+Tab', () => {
    render(
      <Overlay withInitialFocus>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    act(() => {
      document.activeElement.blur();
    });
    tab({ shift: true });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Confirm' }));
  });
});

describe('useFocusTrap: the dynamic cases', () => {
  /** Both buttons disable together, the way `ConfirmDialog` does when `busy` turns on. */
  function BusyOverlay() {
    const [busy, setBusy] = useState(false);
    const containerRef = useRef(null);
    const cancelRef = useRef(null);
    useFocusTrap({ active: true, containerRef, initialFocusRef: cancelRef });
    return (
      <div ref={containerRef} tabIndex={-1} data-testid="overlay">
        <button type="button" ref={cancelRef} disabled={busy}>
          Cancel
        </button>
        <button type="button" disabled={busy}>
          Confirm
        </button>
        <button type="button" data-testid="go-busy" onClick={() => setBusy(true)}>
          Submit
        </button>
      </div>
    );
  }

  it('keeps focus inside when the focused element becomes disabled', () => {
    render(<BusyOverlay />);
    const overlay = screen.getByTestId('overlay');

    act(() => {
      screen.getByTestId('go-busy').click();
    });

    // Cancel and Confirm are now disabled; only "Submit" is left in the cycle.
    tab();
    expect(overlay.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).toBe(screen.getByTestId('go-busy'));
    tab();
    expect(document.activeElement).toBe(screen.getByTestId('go-busy'));
  });

  /** Content swapped under the trap, the way §8.3's deploy flow swaps its steps. */
  function SteppedOverlay() {
    const [step, setStep] = useState(1);
    const containerRef = useRef(null);
    useFocusTrap({ active: true, containerRef });
    return (
      <div ref={containerRef} tabIndex={-1} data-testid="overlay">
        <button type="button" onClick={() => setStep(2)}>
          {`Step ${step}`}
        </button>
        {step === 2 ? <input aria-label="I understand" type="checkbox" /> : null}
      </div>
    );
  }

  it('includes controls that appear after the overlay opened', () => {
    render(<SteppedOverlay />);
    expect(collectFocusable(screen.getByTestId('overlay'))).toHaveLength(1);

    act(() => {
      screen.getByRole('button', { name: 'Step 1' }).click();
    });

    const overlay = screen.getByTestId('overlay');
    expect(collectFocusable(overlay)).toHaveLength(2);
    tab();
    expect(document.activeElement).toBe(screen.getByRole('checkbox', { name: 'I understand' }));
    tab();
    expect(overlay.contains(document.activeElement)).toBe(true);
  });

  it('pulls focus back when something outside steals it', () => {
    const outside = document.createElement('button');
    outside.textContent = 'Outside';
    document.body.appendChild(outside);

    render(
      <Overlay withInitialFocus>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    const overlay = screen.getByTestId('overlay');

    act(() => {
      outside.focus();
    });

    expect(overlay.contains(document.activeElement)).toBe(true);
    outside.remove();
  });
});

describe('useFocusTrap: Escape', () => {
  it('calls onEscape and consumes the key', () => {
    let escapes = 0;
    render(
      <Overlay withInitialFocus onEscape={() => { escapes += 1; }}>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    const notCancelled = fireEvent.keyDown(document.activeElement, { key: 'Escape' });
    expect(escapes).toBe(1);
    expect(notCancelled).toBe(false);
  });

  it('is inert when no handler is given — which is how `busy` refuses to cancel', () => {
    render(
      <Overlay withInitialFocus>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    const notCancelled = fireEvent.keyDown(document.activeElement, { key: 'Escape' });
    expect(notCancelled).toBe(true);
  });

  it('ignores other keys', () => {
    let escapes = 0;
    render(
      <Overlay withInitialFocus onEscape={() => { escapes += 1; }}>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    fireEvent.keyDown(document.activeElement, { key: 'Enter' });
    fireEvent.keyDown(document.activeElement, { key: 'a' });
    expect(escapes).toBe(0);
  });
});

describe('useFocusTrap: closing', () => {
  it('returns focus to the opener', () => {
    const opener = document.createElement('button');
    opener.textContent = 'Deploy';
    document.body.appendChild(opener);
    opener.focus();
    expect(document.activeElement).toBe(opener);

    const view = render(
      <Overlay withInitialFocus>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }));

    view.unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it('returns focus to the opener when the trap is merely deactivated', () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();

    const view = render(
      <Overlay active withInitialFocus>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    view.rerender(
      <Overlay active={false} withInitialFocus>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it('leaves focus alone when the opener has been removed from the document', () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();

    const view = render(
      <Overlay withInitialFocus>
        <button type="button">Confirm</button>
      </Overlay>,
    );
    opener.remove();
    // Must not throw, and must not focus a detached node.
    expect(() => view.unmount()).not.toThrow();
    expect(document.contains(document.activeElement)).toBe(true);
  });
});

describe('useFocusTrap: the rest of the app', () => {
  it('marks other body children aria-hidden while open and restores them after', () => {
    const app = document.createElement('div');
    app.id = 'app-behind';
    document.body.appendChild(app);

    const alreadyHidden = document.createElement('div');
    alreadyHidden.setAttribute('aria-hidden', 'true');
    document.body.appendChild(alreadyHidden);

    const view = render(<Overlay withInitialFocus />);

    expect(app.getAttribute('aria-hidden')).toBe('true');
    // The container's own subtree is never hidden — that would hide the overlay from the
    // assistive technology it exists for.
    expect(screen.getByTestId('overlay').closest('[aria-hidden="true"]')).toBeNull();

    view.unmount();

    // Restored exactly: an attribute we added is removed, one that was already there stays.
    expect(app.hasAttribute('aria-hidden')).toBe(false);
    expect(alreadyHidden.getAttribute('aria-hidden')).toBe('true');

    app.remove();
    alreadyHidden.remove();
  });

  it('leaves the background alone when asked not to hide it', () => {
    const app = document.createElement('div');
    document.body.appendChild(app);

    render(<Overlay withInitialFocus hideBackground={false} />);
    expect(app.hasAttribute('aria-hidden')).toBe(false);
    app.remove();
  });
});
