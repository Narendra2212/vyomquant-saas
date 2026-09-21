/**
 * `ds/CommandButton` — vyomquant-ui-redesign task 6.23.
 * Requirements 1.2, 15.3, 18.1, 18.4. design.md §5.1.
 *
 * The two assertions this component exists for are the two throws: `disabled` without a
 * reason (Requirement 15.3) and no accessible name (Requirement 18.4).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { RefreshCw } from 'lucide-react';

import {
  COMMAND_INTENTS,
  CommandButton,
  hasAccessibleText,
} from '../../../src/components/ds/CommandButton';
import { resetOverlayRegistry } from '../../../src/components/ds/overlayRegistry';

const control = () => document.querySelector('[data-ds="command-button"]');
const reason = () => document.querySelector('[data-ds="command-button-reason"]');

beforeEach(() => {
  resetOverlayRegistry();
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  resetOverlayRegistry();
});

describe('CommandButton: Requirement 15.3 — a disabled control says why', () => {
  it('throws in development when `disabled` carries no `disabledReason`', () => {
    expect(() => render(<CommandButton disabled>Deploy</CommandButton>)).toThrow(
      /`disabled` but carries no `disabledReason`/,
    );
    expect(() =>
      render(
        <CommandButton disabled disabledReason="  ">
          Deploy
        </CommandButton>,
      ),
    ).toThrow(/`disabled` but carries no `disabledReason`/);
  });

  it('logs and renders in production rather than losing the page', () => {
    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<CommandButton disabled>Deploy</CommandButton>);
    expect(screen.getByRole('button', { name: 'Deploy' }).disabled).toBe(true);
    expect(spy).toHaveBeenCalled();
  });

  it('displays the reason and points the control at it', () => {
    render(
      <CommandButton disabled disabledReason="Connect an exchange account first">
        Deploy
      </CommandButton>,
    );
    expect(reason().textContent).toBe('Connect an exchange account first');
    expect(control().getAttribute('aria-describedby')).toBe(reason().id);
  });

  it('does not demand a reason for `loading`, which explains itself', () => {
    // `loadingLabel` is the explanation; asking for a second one would produce
    // `disabledReason="Loading"`, which explains nothing.
    expect(() =>
      render(
        <CommandButton loading loadingLabel="Deploying…">
          Deploy
        </CommandButton>,
      ),
    ).not.toThrow();
    expect(reason()).toBeNull();
  });
});

describe('CommandButton: Requirement 18.4 — every control has an accessible name', () => {
  it('throws for an icon-only button with no aria-label', () => {
    expect(() => render(<CommandButton icon={RefreshCw} />)).toThrow(
      /icon with no text and no `aria-label`/,
    );
  });

  it('accepts an icon-only button that is labelled', () => {
    render(<CommandButton icon={RefreshCw} aria-label="Refresh positions" />);
    expect(screen.getByRole('button', { name: 'Refresh positions' })).toBeTruthy();
  });

  it('throws for a button with neither children nor a label', () => {
    expect(() => render(<CommandButton />)).toThrow(/neither children nor an `aria-label`/);
  });

  it('takes an element child at its word and a blank string as blank', () => {
    expect(hasAccessibleText('Deploy')).toBe(true);
    expect(hasAccessibleText('   ')).toBe(false);
    expect(hasAccessibleText(0)).toBe(true);
    expect(hasAccessibleText(null)).toBe(false);
    expect(hasAccessibleText([null, false, 'Deploy'])).toBe(true);
    expect(hasAccessibleText([null, false])).toBe(false);
    expect(() => render(<CommandButton icon={RefreshCw}><span>Refresh</span></CommandButton>)).not.toThrow();
  });
});

describe('CommandButton: intent', () => {
  it('accepts the five intents design.md §5.1 names and publishes the one in force', () => {
    expect(COMMAND_INTENTS).toEqual(['primary', 'secondary', 'ghost', 'destructive', 'live']);
    for (const intent of COMMAND_INTENTS) {
      const { unmount } = render(<CommandButton intent={intent}>Act</CommandButton>);
      expect(control().getAttribute('data-ds-intent')).toBe(intent);
      unmount();
    }
  });

  it('throws on an unrecognised intent and renders primary in production', () => {
    expect(() => render(<CommandButton intent="cyan">Act</CommandButton>)).toThrow(
      /`intent` must be one of/,
    );

    vi.stubEnv('DEV', false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<CommandButton intent="cyan">Act</CommandButton>);
    expect(control().getAttribute('data-ds-intent')).toBe('primary');
  });
});

describe('CommandButton: loading', () => {
  it('reads its loading label, is inoperable and is marked busy', () => {
    const onClick = vi.fn();
    render(
      <CommandButton loading loadingLabel="Deploying…" onClick={onClick}>
        Deploy
      </CommandButton>,
    );
    const button = screen.getByRole('button', { name: 'Deploying…' });
    expect(button.getAttribute('aria-busy')).toBe('true');
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('keeps its own label when no loading label is given', () => {
    render(
      <CommandButton loading onClick={() => {}}>
        Deploy
      </CommandButton>,
    );
    expect(screen.getByRole('button', { name: 'Deploy' }).getAttribute('aria-busy')).toBe('true');
  });

  it('carries no busy marking when it is not loading', () => {
    render(<CommandButton>Deploy</CommandButton>);
    expect(control().getAttribute('aria-busy')).toBeNull();
  });
});

describe('CommandButton: the action', () => {
  it('calls onClick on activation', () => {
    const onClick = vi.fn();
    render(<CommandButton onClick={onClick}>Deploy</CommandButton>);
    fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('refuses a programmatic click on a disabled control', () => {
    // `disabled` on the element is a rendering; the handler's guard is the rule.
    const onClick = vi.fn();
    render(
      <CommandButton disabled disabledReason="No exchange account" onClick={onClick}>
        Deploy
      </CommandButton>,
    );
    control().click();
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('CommandButton: the optional confirmation', () => {
  const confirm = Object.freeze({
    title: 'Stop live deployment?',
    description: 'The strategy stops placing orders.',
    confirmLabel: 'Stop deployment',
    cancelLabel: 'Keep running',
  });

  it('opens the dialog instead of acting, and acts only on confirm', () => {
    const onClick = vi.fn();
    render(
      <CommandButton intent="destructive" confirm={confirm} onClick={onClick}>
        Stop
      </CommandButton>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(onClick).not.toHaveBeenCalled();

    const dialog = screen.getByRole('dialog');
    expect(dialog.getAttribute('data-ds-intent')).toBe('destructive');

    fireEvent.click(screen.getByRole('button', { name: 'Stop deployment' }));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('cancels without acting', () => {
    const onClick = vi.fn();
    const onCancel = vi.fn();
    render(
      <CommandButton confirm={{ ...confirm, onCancel }} onClick={onClick}>
        Stop
      </CommandButton>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    fireEvent.click(screen.getByRole('button', { name: 'Keep running' }));
    expect(onClick).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('inherits a risk intent for the dialog and confirms neutrally otherwise', () => {
    const { unmount } = render(
      <CommandButton intent="live" confirm={confirm} onClick={() => {}}>
        Deploy live
      </CommandButton>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Deploy live' }));
    expect(screen.getByRole('dialog').getAttribute('data-ds-intent')).toBe('live');
    unmount();
    resetOverlayRegistry();

    render(
      <CommandButton intent="primary" confirm={confirm} onClick={() => {}}>
        Save
      </CommandButton>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(screen.getByRole('dialog').getAttribute('data-ds-intent')).toBe('neutral');
  });

  it('renders no dialog until the control is activated', () => {
    render(
      <CommandButton confirm={confirm} onClick={() => {}}>
        Stop
      </CommandButton>,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
