/**
 * `ds/Field` — vyomquant-ui-redesign task 6.15. Requirements 15.1, 15.2, 15.3.
 *
 * The two dev-time throws and the never-placeholder-as-label rule are the point of
 * the component, so they are the bulk of what is asserted here. P28 (task 6.16)
 * generalises these over generated descriptors; these are the named cases.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { Field, NUMERIC_TYPES, resolveInputType, shouldShowError } from '../../../src/components/ds/Field';

const mount = (props) => render(<Field {...props} />);

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('Field: Requirement 15.1 — a label, and never the placeholder instead', () => {
  it('renders a visible label associated with the control', () => {
    mount({ id: 'initial-capital', label: 'Initial simulated capital', placeholder: '100000.00' });
    const input = screen.getByLabelText('Initial simulated capital');
    expect(input.id).toBe('initial-capital');
    // Visible, not sr-only: the requirement asks for an explicit text label.
    const label = document.querySelector('label[for="initial-capital"]');
    expect(label.textContent).toBe('Initial simulated capital');
    expect(label.className).not.toContain('sr-only');
  });

  it('throws in development without a label', () => {
    expect(() => mount({ id: 'amount', placeholder: '100000.00' })).toThrow(/`label` is required/);
    expect(() => mount({ id: 'amount', label: '   ', placeholder: '100000.00' })).toThrow(/`label` is required/);
  });

  it('never derives an accessible name from the placeholder', () => {
    // `Inp` in ui-legacy/primitives.jsx does `aria-label={ariaLabel || lbl || ph}`.
    mount({ id: 'amount', label: 'Amount', placeholder: '100000.00' });
    const input = document.getElementById('amount');
    expect(input.getAttribute('aria-label')).toBeNull();
    expect(input.getAttribute('placeholder')).toBe('100000.00');
    // The placeholder is not the name, and querying by it finds nothing.
    expect(screen.queryByLabelText('100000.00')).toBeNull();
    expect(screen.getByLabelText('Amount')).toBe(input);
  });

  it('keeps the label when the placeholder is absent', () => {
    mount({ id: 'amount', label: 'Amount' });
    expect(document.getElementById('amount').getAttribute('placeholder')).toBeNull();
    expect(screen.getByLabelText('Amount')).toBeTruthy();
  });

  it('generates a unique id per instance rather than deriving one from the label', () => {
    // `Inp` built its id from the label text, so two forms both saying "Amount"
    // produced two elements with the same id and one label pointing at both.
    render(
      <>
        <Field label="Amount" />
        <Field label="Amount" />
      </>,
    );
    const inputs = [...document.querySelectorAll('input')];
    expect(inputs).toHaveLength(2);
    expect(inputs[0].id).not.toBe(inputs[1].id);
    expect(document.querySelector(`label[for="${inputs[0].id}"]`)).toBeTruthy();
    expect(document.querySelector(`label[for="${inputs[1].id}"]`)).toBeTruthy();
  });
});

describe('Field: Requirement 15.3 — a disabled control states why', () => {
  it('throws in development when disabled without a reason', () => {
    expect(() => mount({ id: 'capital', label: 'Initial capital', disabled: true })).toThrow(
      /`disabled` requires `disabledReason`/,
    );
    expect(() => mount({ id: 'capital', label: 'Initial capital', disabled: true, disabledReason: ' ' })).toThrow(
      /`disabled` requires `disabledReason`/,
    );
  });

  it('renders the reason as visible text and in the accessible description', () => {
    mount({
      id: 'capital',
      label: 'Initial capital',
      disabled: true,
      disabledReason: 'Stop the running session before changing capital',
    });
    const input = document.getElementById('capital');
    expect(input.disabled).toBe(true);
    expect(input.getAttribute('aria-disabled')).toBe('true');

    // Visible, for a trader reading.
    const visible = screen.getByText('Stop the running session before changing capital');
    expect(visible).toBeTruthy();

    // And described, for a trader listening — the same element, referenced.
    const describedBy = input.getAttribute('aria-describedby');
    expect(describedBy).toContain(visible.id);
  });

  it('names the field in the failure so the call site is findable', () => {
    expect(() => mount({ label: 'Initial capital', disabled: true })).toThrow(/Initial capital/);
  });

  it('logs instead of throwing in production, and still renders the control', () => {
    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mount({ id: 'capital', label: 'Initial capital', disabled: true });
    expect(document.getElementById('capital')).toBeTruthy();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe('Field: Requirement 15.2 — the message, on the offending field alone', () => {
  const INVALID = Object.freeze({
    id: 'capital',
    label: 'Initial capital',
    value: '100000.555',
    invalid: true,
    error: 'USD is held to 2 decimal places.',
  });

  it('throws in development for an error treatment with no message', () => {
    expect(() => mount({ id: 'capital', label: 'Initial capital', invalid: true })).toThrow(
      /`invalid` requires `error`/,
    );
  });

  it('links the message to the input and marks it invalid, once shown', () => {
    mount({ ...INVALID, submitted: true });
    const input = document.getElementById('capital');
    expect(input.getAttribute('aria-invalid')).toBe('true');
    const message = screen.getByText('USD is held to 2 decimal places.');
    expect(input.getAttribute('aria-describedby')).toContain(message.id);
  });

  it('carries the error on the offending field alone', () => {
    render(
      <>
        <Field id="capital" label="Initial capital" invalid error="Too many decimal places." submitted />
        <Field id="leverage" label="Leverage" value="2" />
      </>,
    );
    expect(document.getElementById('capital').getAttribute('aria-invalid')).toBe('true');
    expect(document.getElementById('leverage').getAttribute('aria-invalid')).toBeNull();
    expect(document.querySelectorAll('[data-field-invalid="true"]')).toHaveLength(1);
    expect(document.getElementById('leverage').getAttribute('aria-describedby')).toBeNull();
  });

  it('is not a live region — nine invalid fields would announce nine times', () => {
    mount({ ...INVALID, submitted: true });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
    expect(document.querySelector('[aria-live]')).toBeNull();
  });
});

describe('Field: validation shows on blur and submit, not per keystroke', () => {
  it('is the rule shouldShowError encodes', () => {
    const base = { hasError: true, submitted: false, blurred: false, editing: false };
    expect(shouldShowError({ ...base })).toBe(false);
    expect(shouldShowError({ ...base, blurred: true })).toBe(true);
    expect(shouldShowError({ ...base, blurred: true, editing: true })).toBe(false);
    expect(shouldShowError({ ...base, submitted: true })).toBe(true);
    expect(shouldShowError({ ...base, hasError: false, submitted: true, blurred: true })).toBe(false);
  });

  it('stays silent while the trader types 100000 through 1, 10 and 100', () => {
    // The page computes `error` purely from the value, so it is present on every one
    // of these renders. Nothing should be on screen until the trader stops.
    const onChange = vi.fn();
    const view = render(
      <Field id="capital" label="Initial capital" value="" error="Below the minimum." onChange={onChange} />,
    );
    const input = document.getElementById('capital');

    ['1', '10', '100', '100000'].forEach((next) => {
      fireEvent.change(input, { target: { value: next } });
      expect(screen.queryByText('Below the minimum.')).toBeNull();
      expect(input.getAttribute('aria-invalid')).toBeNull();
      view.rerender(
        <Field id="capital" label="Initial capital" value={next} error="Below the minimum." onChange={onChange} />,
      );
    });

    expect(onChange).toHaveBeenCalledTimes(4);
  });

  it('shows the message on blur and withdraws it once the trader resumes editing', () => {
    mount({ id: 'capital', label: 'Initial capital', value: '1', error: 'Below the minimum.' });
    const input = document.getElementById('capital');

    fireEvent.blur(input);
    expect(screen.getByText('Below the minimum.')).toBeTruthy();
    expect(input.getAttribute('aria-invalid')).toBe('true');

    // They are already fixing it; repeating the verdict mid-repair is noise.
    fireEvent.change(input, { target: { value: '10' } });
    expect(screen.queryByText('Below the minimum.')).toBeNull();
    expect(input.getAttribute('aria-invalid')).toBeNull();
  });

  it('keeps the message after a submit even while the field is being edited', () => {
    mount({ id: 'capital', label: 'Initial capital', value: '1', error: 'Below the minimum.', submitted: true });
    const input = document.getElementById('capital');
    expect(screen.getByText('Below the minimum.')).toBeTruthy();
    fireEvent.change(input, { target: { value: '10' } });
    expect(screen.getByText('Below the minimum.')).toBeTruthy();
  });

  it('calls the caller onBlur as well as its own bookkeeping', () => {
    const onBlur = vi.fn();
    mount({ id: 'capital', label: 'Initial capital', error: 'Below the minimum.', onBlur });
    fireEvent.blur(document.getElementById('capital'));
    expect(onBlur).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Below the minimum.')).toBeTruthy();
  });
});

describe('Field: numbers stay strings until someone parses them', () => {
  it('renders every numeric type as text with a decimal keypad', () => {
    expect(NUMERIC_TYPES).toEqual(['number', 'decimal', 'currency']);
    NUMERIC_TYPES.forEach((type) => {
      expect(resolveInputType(type)).toEqual({ type: 'text', inputMode: 'decimal' });
    });
    expect(resolveInputType('search')).toEqual({ type: 'search', inputMode: undefined });
    expect(resolveInputType()).toEqual({ type: 'text', inputMode: undefined });
  });

  it('never puts type="number" in the DOM', () => {
    mount({ id: 'capital', label: 'Initial capital', type: 'number', value: '100000.00' });
    const input = document.getElementById('capital');
    expect(input.getAttribute('type')).toBe('text');
    expect(input.getAttribute('inputmode')).toBe('decimal');
    expect(input.getAttribute('autocomplete')).toBe('off');
  });

  it('hands the exact string typed to onChange, unrounded and unreformatted', () => {
    // The discipline `paperTradingFormat.js::parseCapitalToMinor` depends on: it
    // REFUSES 100000.555 rather than rounding it, which it cannot do if the field
    // has already normalised the value on the way in.
    const seen = [];
    mount({
      id: 'capital',
      label: 'Initial capital',
      type: 'decimal',
      value: '',
      onChange: (event) => seen.push(event.target.value),
    });
    const input = document.getElementById('capital');
    ['0', '00', '100000.555', '1,000.10', '19.99'].forEach((text) => {
      fireEvent.change(input, { target: { value: text } });
    });
    expect(seen).toEqual(['0', '00', '100000.555', '1,000.10', '19.99']);
  });

  it('renders a unit suffix inside the accessible description', () => {
    mount({ id: 'capital', label: 'Initial capital', type: 'decimal', unit: 'USD', hint: 'At most 2 decimals' });
    const input = document.getElementById('capital');
    const describedBy = input.getAttribute('aria-describedby').split(' ');
    // Hint first, then the unit — the order they are read in.
    expect(describedBy).toEqual([`${input.id}-hint`, `${input.id}-unit`]);
    expect(screen.getByText('USD')).toBeTruthy();
  });
});

describe('Field: select and required', () => {
  it('renders a labelled select when given options', () => {
    mount({
      id: 'decision',
      label: 'Decision',
      options: [
        { value: '', label: 'Any' },
        { value: 'BUY', label: 'Buy' },
      ],
    });
    const select = screen.getByLabelText('Decision');
    expect(select.tagName).toBe('SELECT');
    expect([...select.options].map((option) => option.textContent)).toEqual(['Any', 'Buy']);
  });

  it('throws for an option with no readable label', () => {
    expect(() => mount({ id: 'decision', label: 'Decision', options: [{ value: 'BUY' }] })).toThrow(
      /every entry in `options` needs a non-empty `label`/,
    );
  });

  it('marks a required field in the DOM and on screen', () => {
    mount({ id: 'capital', label: 'Initial capital', required: true });
    const input = document.getElementById('capital');
    expect(input.required).toBe(true);
    expect(input.getAttribute('aria-required')).toBe('true');
    // The word, not an asterisk — an asterisk needs a legend elsewhere to mean anything.
    expect(screen.getByText('Required')).toBeTruthy();
  });

  it('omits aria-describedby entirely when there is nothing to describe', () => {
    mount({ id: 'capital', label: 'Initial capital' });
    expect(document.getElementById('capital').getAttribute('aria-describedby')).toBeNull();
  });
});
