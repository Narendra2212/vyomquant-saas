/**
 * `ds/Tooltip` — vyomquant-ui-redesign task 6.23.
 * Requirements 1.2, 18.1, 18.4. design.md §5.2, §11.4.
 *
 * The first block is the reason this component exists: it opens on FOCUS, not only on
 * hover. A hover-only tooltip passes every "does it appear" test written with a mouse and
 * is unreachable for a trader who does not use one, which fails Requirement 18.1.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { Tooltip } from '../../../src/components/ds/Tooltip';

const HINT = 'Realised + unrealised since account open';

const bubble = () => document.querySelector('[data-ds="tooltip"]');
const isOpen = () => bubble().getAttribute('data-tooltip-open') === 'true';

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe('Tooltip: it opens on keyboard focus, not only on hover', () => {
  it('opens on focus and closes on blur', () => {
    render(
      <Tooltip content={HINT}>
        <button type="button">Total P&L</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole('button', { name: 'Total P&L' });

    expect(isOpen()).toBe(false);
    fireEvent.focus(trigger);
    expect(isOpen()).toBe(true);
    fireEvent.blur(trigger);
    expect(isOpen()).toBe(false);
  });

  it('opens on mouseenter and closes on mouseleave', () => {
    render(
      <Tooltip content={HINT}>
        <button type="button">Total P&L</button>
      </Tooltip>,
    );
    const wrapper = document.querySelector('[data-ds="tooltip-trigger"]');

    fireEvent.mouseEnter(wrapper);
    expect(isOpen()).toBe(true);
    fireEvent.mouseLeave(wrapper);
    expect(isOpen()).toBe(false);
  });

  it('stays open when the pointer leaves while focus is still on the trigger', () => {
    // Hover and focus are two independent reasons to be open. Collapsing them into one
    // boolean is what makes a tooltip vanish under the pointer while a keyboard user is
    // still reading it.
    render(
      <Tooltip content={HINT}>
        <button type="button">Total P&L</button>
      </Tooltip>,
    );
    const wrapper = document.querySelector('[data-ds="tooltip-trigger"]');
    const trigger = screen.getByRole('button', { name: 'Total P&L' });

    fireEvent.focus(trigger);
    fireEvent.mouseEnter(wrapper);
    fireEvent.mouseLeave(wrapper);
    expect(isOpen()).toBe(true);

    fireEvent.blur(trigger);
    expect(isOpen()).toBe(false);
  });

  it('closes on Escape and reopens on the next focus', () => {
    render(
      <Tooltip content={HINT}>
        <button type="button">Total P&L</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole('button', { name: 'Total P&L' });

    fireEvent.focus(trigger);
    expect(isOpen()).toBe(true);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(isOpen()).toBe(false);

    fireEvent.blur(trigger);
    fireEvent.focus(trigger);
    expect(isOpen()).toBe(true);
  });
});

describe('Tooltip: it describes the trigger and does not rename it', () => {
  it('uses aria-describedby, leaving the trigger its own accessible name', () => {
    render(
      <Tooltip content={HINT}>
        <button type="button">Total P&L</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole('button', { name: 'Total P&L' });

    // The name is still the label. An `aria-label` carrying the hint would have replaced
    // it, and the trader would never hear what the figure is.
    expect(trigger.getAttribute('aria-label')).toBeNull();
    const described = trigger.getAttribute('aria-describedby');
    expect(described).toBeTruthy();
    expect(document.getElementById(described).textContent).toBe(HINT);
  });

  it('keeps the description resolvable while closed', () => {
    // `aria-describedby` pointing at an element that only exists while the tooltip is
    // open is a reference that resolves to nothing for as long as it matters.
    render(
      <Tooltip content={HINT}>
        <button type="button">Total P&L</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole('button', { name: 'Total P&L' });
    expect(isOpen()).toBe(false);
    expect(document.getElementById(trigger.getAttribute('aria-describedby'))).toBeTruthy();
    // Never hidden from assistive technology — that would defeat the whole element.
    expect(bubble().getAttribute('aria-hidden')).toBeNull();
    expect(bubble().className).toBe('sr-only');
  });

  it('appends to a description the caller already set rather than evicting it', () => {
    render(
      <Tooltip content={HINT}>
        <input aria-label="Capital" aria-describedby="capital-hint" />
      </Tooltip>,
    );
    const described = screen.getByRole('textbox').getAttribute('aria-describedby');
    expect(described.split(' ')).toHaveLength(2);
    expect(described.startsWith('capital-hint ')).toBe(true);
  });

  it('carries role="tooltip"', () => {
    render(
      <Tooltip content={HINT}>
        <button type="button">Total P&L</button>
      </Tooltip>,
    );
    expect(bubble().getAttribute('role')).toBe('tooltip');
  });
});

describe('Tooltip: making a non-interactive trigger reachable', () => {
  it('adds a tab stop to a span, which cannot otherwise take focus', () => {
    // `ds/Metric` hangs its `hint` on a `<span>` label. Without a tabIndex the
    // description is never announced, because the element cannot receive focus.
    render(
      <Tooltip content={HINT}>
        <span>Total P&L</span>
      </Tooltip>,
    );
    expect(screen.getByText('Total P&L').getAttribute('tabindex')).toBe('0');
  });

  it('leaves a natively focusable trigger alone, so no control gains a second stop', () => {
    render(
      <Tooltip content={HINT}>
        <button type="button">Deploy</button>
      </Tooltip>,
    );
    expect(screen.getByRole('button', { name: 'Deploy' }).getAttribute('tabindex')).toBeNull();
  });

  it('respects a tabIndex the caller set', () => {
    render(
      <Tooltip content={HINT}>
        <span tabIndex={-1}>Total P&L</span>
      </Tooltip>,
    );
    expect(screen.getByText('Total P&L').getAttribute('tabindex')).toBe('-1');
  });
});

describe('Tooltip: nothing to say costs nothing', () => {
  it('renders the children untouched with no bubble and no tab stop', () => {
    // `<Tooltip content={maybeHint}>` has to be safe to write when the hint is optional:
    // a tooltip with no content must not cost a keystroke.
    render(
      <Tooltip content={undefined}>
        <span>Total P&L</span>
      </Tooltip>,
    );
    const text = screen.getByText('Total P&L');
    expect(text.getAttribute('tabindex')).toBeNull();
    expect(text.getAttribute('aria-describedby')).toBeNull();
    expect(bubble()).toBeNull();
    expect(document.querySelector('[data-ds="tooltip-trigger"]')).toBeNull();
  });

  it('treats the empty string the same way', () => {
    render(
      <Tooltip content="">
        <span>Total P&L</span>
      </Tooltip>,
    );
    expect(bubble()).toBeNull();
  });
});

describe('Tooltip: the trigger contract', () => {
  it('throws in development when the trigger is not a single element', () => {
    expect(() => render(<Tooltip content={HINT}>Total P&L</Tooltip>)).toThrow(
      /must be exactly one element/,
    );
  });

  it('renders the text plainly in production rather than losing it', () => {
    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<Tooltip content={HINT}>Total P&L</Tooltip>);
    expect(screen.getByText('Total P&L')).toBeTruthy();
    expect(spy).toHaveBeenCalled();
  });
});
