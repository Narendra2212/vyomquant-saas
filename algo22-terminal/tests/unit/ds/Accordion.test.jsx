/**
 * `ds/Accordion` — vyomquant-ui-redesign task 6.15. Requirement 15.6.
 *
 * The declared advanced set is the whole mechanism; P31 (task 6.17) generalises the
 * set equality over generated descriptors, and these are the named cases plus the
 * checks that hold the declaration and the render together.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { Accordion, ADVANCED_FIELDS_ATTR, partitionAdvanced } from '../../../src/components/ds/Accordion';
import { Field } from '../../../src/components/ds/Field';

/** A form descriptor of the shape a page declares once and both sides read. */
const PAPER_SESSION_FORM = Object.freeze([
  { id: 'initial-capital', label: 'Initial simulated capital' },
  { id: 'market', label: 'Market' },
  { id: 'slippage-bps', label: 'Slippage', advanced: true },
  { id: 'fee-bps', label: 'Taker fee', advanced: true },
]);

const { standard, advanced } = partitionAdvanced(PAPER_SESSION_FORM);

const mountForm = (props) =>
  render(
    <form>
      {standard.map((id) => (
        <Field key={id} id={id} label={PAPER_SESSION_FORM.find((f) => f.id === id).label} />
      ))}
      <Accordion title="Advanced settings" fields={advanced} {...props}>
        {advanced.map((id) => (
          <Field key={id} id={id} label={PAPER_SESSION_FORM.find((f) => f.id === id).label} />
        ))}
      </Accordion>
    </form>,
  );

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('partitionAdvanced: the declared set is data', () => {
  it('splits a descriptor into the two sets, preserving order', () => {
    expect(standard).toEqual(['initial-capital', 'market']);
    expect(advanced).toEqual(['slippage-bps', 'fee-bps']);
  });

  it('is a partition — no id lands on both sides, and duplicates collapse', () => {
    const result = partitionAdvanced([
      { id: 'a' },
      { id: 'a', advanced: true },
      { id: 'b', advanced: true },
      { id: 'b' },
    ]);
    expect(result.standard).toEqual(['a']);
    expect(result.advanced).toEqual(['b']);
    const overlap = result.standard.filter((id) => result.advanced.includes(id));
    expect(overlap).toEqual([]);
  });

  it('ignores entries with no usable id rather than inventing one', () => {
    expect(partitionAdvanced([{ label: 'no id' }, null, 'nope', { id: '  ' }])).toEqual({
      standard: [],
      advanced: [],
    });
    expect(partitionAdvanced(undefined)).toEqual({ standard: [], advanced: [] });
  });
});

describe('Accordion: Requirement 15.6 — advanced fields start collapsed', () => {
  it('collapses exactly the declared advanced set on first render', () => {
    mountForm();
    const rendered = [...document.querySelectorAll('[data-field-id]')].map((node) =>
      node.getAttribute('data-field-id'),
    );
    // The standard fields are on screen; the advanced ones are not rendered at all.
    expect(rendered).toEqual([...standard]);
    const collapsed = advanced.filter((id) => !rendered.includes(id));
    expect(collapsed).toEqual([...advanced]);
  });

  it('publishes the declared set so the collapsed set can be checked against it', () => {
    mountForm();
    const region = document.querySelector(`[${ADVANCED_FIELDS_ATTR}]`);
    expect(region.getAttribute(ADVANCED_FIELDS_ATTR).split(' ')).toEqual([...advanced]);
    expect(region.getAttribute('data-accordion-open')).toBe('false');
  });

  it('defaults to closed, and defaultOpen is the only way out', () => {
    mountForm();
    expect(screen.getByRole('button', { name: /Advanced settings/ }).getAttribute('aria-expanded')).toBe('false');
    cleanup();
    mountForm({ defaultOpen: true });
    expect(screen.getByRole('button', { name: /Advanced settings/ }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByLabelText('Slippage')).toBeTruthy();
  });

  it('mounts the advanced fields on open and unmounts them again on close', () => {
    // Not hidden with CSS: a max-height:0 panel is still tabbable, which drops a
    // keyboard user into a field they cannot see, and leaves nothing to measure.
    mountForm();
    const toggle = screen.getByRole('button', { name: /Advanced settings/ });
    expect(screen.queryByLabelText('Slippage')).toBeNull();

    fireEvent.click(toggle);
    expect(screen.getByLabelText('Slippage')).toBeTruthy();
    expect(screen.getByLabelText('Taker fee')).toBeTruthy();
    const panel = document.getElementById(toggle.getAttribute('aria-controls'));
    expect(panel.getAttribute('role')).toBe('region');

    fireEvent.click(toggle);
    expect(screen.queryByLabelText('Slippage')).toBeNull();
    expect(document.getElementById(toggle.getAttribute('aria-controls'))).toBeNull();
  });

  it('throws for a field that collapses without having been declared', () => {
    // The case a set-equality check reading only the declaration cannot see.
    expect(() =>
      render(
        <Accordion title="Advanced settings" fields={['slippage-bps']} defaultOpen>
          <Field id="fee-bps" label="Taker fee" />
        </Accordion>,
      ),
    ).toThrow(/not in that accordion's declared `fields` set/);
  });

  it('accepts a declared field and leaves fields outside any accordion alone', () => {
    expect(() =>
      render(
        <>
          <Field id="initial-capital" label="Initial simulated capital" />
          <Accordion title="Advanced settings" fields={['slippage-bps']} defaultOpen>
            <Field id="slippage-bps" label="Slippage" />
          </Accordion>
        </>,
      ),
    ).not.toThrow();
  });
});

describe('Accordion: its own contract', () => {
  it('requires a title, because a chevron is not a name', () => {
    expect(() => render(<Accordion fields={['a']}>x</Accordion>)).toThrow(/`title` is required/);
  });

  it('requires a non-empty declared field set', () => {
    expect(() => render(<Accordion title="Advanced settings">x</Accordion>)).toThrow(/`fields` is required/);
    expect(() => render(<Accordion title="Advanced settings" fields={[]}>x</Accordion>)).toThrow(
      /`fields` is required/,
    );
    expect(() => render(<Accordion title="Advanced settings" fields={['a', '']}>x</Accordion>)).toThrow(
      /`fields` is required/,
    );
  });

  it('renders in production without the declaration rather than losing the panel', () => {
    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<Accordion title="Advanced settings">x</Accordion>);
    expect(screen.getByRole('button', { name: /Advanced settings/ })).toBeTruthy();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it('names the region from a heading, and says how much is inside', () => {
    mountForm();
    const heading = document.querySelector('h3');
    expect(heading.querySelector('button')).toBeTruthy();
    expect(screen.getByText('2 settings')).toBeTruthy();
    cleanup();
    render(
      <Accordion title="Advanced settings" fields={['slippage-bps']} level={2}>
        <Field id="slippage-bps" label="Slippage" />
      </Accordion>,
    );
    expect(document.querySelector('h2')).toBeTruthy();
    expect(screen.getByText('1 setting')).toBeTruthy();
  });
});
