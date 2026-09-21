/**
 * `ds/OverflowMenu` — vyomquant-ui-redesign task 17.2.
 * Requirements 4.3, 15.3, 18.1, 18.4. design.md §7.2, §11.7.
 *
 * The load-bearing cases are the two that Requirement 4.3 turns on and that a snapshot
 * would not notice:
 *
 *  1. **The separation exists for a screen reader, not only for a border.** A horizontal
 *     rule is invisible to assistive technology, so the separated entries are also a named
 *     `role="group"`. These tests assert the group and its name, not the rule's colour.
 *  2. **Focus returns to the trigger.** A menu that closes and drops focus to the document
 *     body strands a keyboard user at the top of the page — which, in a forty-row table, is
 *     forty rows away from where they were.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
  MENU_INTENTS,
  OverflowMenu,
  SEPARATED_GROUP_LABEL,
  nextMenuIndex,
} from '../../../src/components/ds/OverflowMenu';

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

const LABEL = 'More actions for Momentum v2';

/** Two inline entries and the two §7.2 separates, with spies for the handlers. */
const spec = (overrides = {}) => {
  const calls = { rename: 0, deploy: 0, archive: 0 };
  const items = [
    { id: 'rename', label: 'Rename', onSelect: () => { calls.rename += 1; } },
    {
      id: 'deploy_live',
      label: 'Deploy live',
      intent: 'live',
      separated: true,
      onSelect: () => { calls.deploy += 1; },
    },
    {
      id: 'archive',
      label: 'Archive strategy',
      intent: 'destructive',
      separated: true,
      onSelect: () => { calls.archive += 1; },
    },
    ...(overrides.extra ?? []),
  ];
  return { calls, items };
};

const trigger = () => screen.getByRole('button', { name: LABEL });
const menu = () => screen.getByRole('menu');

describe('OverflowMenu: the trigger', () => {
  it('carries the row-specific accessible name and reports its state', () => {
    const { items } = spec();
    render(<OverflowMenu label={LABEL} items={items} />);

    expect(trigger().getAttribute('aria-haspopup')).toBe('menu');
    expect(trigger().getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('renders nothing when no entry survives the contract', () => {
    // Every entry is unusable: no label, no handler, not an object. A trigger that opens an
    // empty panel is a control that does nothing.
    render(
      <OverflowMenu
        label={LABEL}
        items={[{ id: 'a' }, { id: 'b', label: 'B' }, null, 'nope']}
      />,
    );
    expect(screen.queryByRole('button', { name: LABEL })).toBeNull();
  });
});

describe('OverflowMenu: Requirement 4.3 — the separation is structural', () => {
  it('puts the separated entries below a divider, inside a named group', async () => {
    const { items } = spec();
    render(<OverflowMenu label={LABEL} items={items} />);
    await userEvent.setup().click(trigger());

    expect(trigger().getAttribute('aria-expanded')).toBe('true');

    // The divider a sighted trader sees…
    expect(within(menu()).getByRole('separator')).toBeTruthy();
    // …and the same fact, said in words.
    const group = within(menu()).getByRole('group', { name: SEPARATED_GROUP_LABEL });
    expect(within(group).getByRole('menuitem', { name: 'Deploy live' })).toBeTruthy();
    expect(within(group).getByRole('menuitem', { name: 'Archive strategy' })).toBeTruthy();
    // The inline entry is NOT in it — otherwise the group would name nothing useful.
    expect(within(group).queryByRole('menuitem', { name: 'Rename' })).toBeNull();
  });

  it('renders the separated entries last, whatever order they are declared in', async () => {
    const items = [
      { id: 'archive', label: 'Archive strategy', separated: true, onSelect: () => {} },
      { id: 'rename', label: 'Rename', onSelect: () => {} },
    ];
    render(<OverflowMenu label={LABEL} items={items} />);
    await userEvent.setup().click(trigger());

    const labels = within(menu()).getAllByRole('menuitem').map((node) => node.textContent);
    expect(labels).toEqual(['Rename', 'Archive strategy']);
  });

  it('marks each entry with its declared treatment and drops an unknown one to neutral', async () => {
    const { items } = spec({
      extra: [{ id: 'weird', label: 'Weird', intent: 'chartreuse', onSelect: () => {} }],
    });
    render(<OverflowMenu label={LABEL} items={items} />);
    await userEvent.setup().click(trigger());

    const intentOf = (name) =>
      within(menu()).getByRole('menuitem', { name }).getAttribute('data-menu-intent');

    expect(intentOf('Deploy live')).toBe('live');
    expect(intentOf('Archive strategy')).toBe('destructive');
    expect(intentOf('Rename')).toBe('neutral');
    // An intent outside the vocabulary is not honoured as a colour.
    expect(intentOf('Weird')).toBe('neutral');
    expect(MENU_INTENTS).toContain(intentOf('Weird'));
  });
});

describe('OverflowMenu: keyboard and focus (Requirement 18.1)', () => {
  it('focuses the first entry on open and the last when opened with ArrowUp', async () => {
    const user = userEvent.setup();
    const { items } = spec();
    const { unmount } = render(<OverflowMenu label={LABEL} items={items} />);

    await user.click(trigger());
    expect(document.activeElement.textContent).toBe('Rename');

    unmount();
    render(<OverflowMenu label={LABEL} items={items} />);
    trigger().focus();
    await user.keyboard('{ArrowUp}');
    expect(document.activeElement.textContent).toBe('Archive strategy');
  });

  it('moves focus with the arrow keys, wrapping at both ends', async () => {
    const user = userEvent.setup();
    const { items } = spec();
    render(<OverflowMenu label={LABEL} items={items} />);

    await user.click(trigger());
    await user.keyboard('{ArrowDown}');
    expect(document.activeElement.textContent).toBe('Deploy live');
    await user.keyboard('{ArrowDown}{ArrowDown}');
    // Wrapped past the end, back to the first entry.
    expect(document.activeElement.textContent).toBe('Rename');
    await user.keyboard('{ArrowUp}');
    expect(document.activeElement.textContent).toBe('Archive strategy');
    await user.keyboard('{Home}');
    expect(document.activeElement.textContent).toBe('Rename');
    await user.keyboard('{End}');
    expect(document.activeElement.textContent).toBe('Archive strategy');
  });

  it('closes on Escape and returns focus to the trigger', async () => {
    const user = userEvent.setup();
    const { items } = spec();
    render(<OverflowMenu label={LABEL} items={items} />);

    await user.click(trigger());
    await user.keyboard('{Escape}');

    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it('runs the entry\'s handler, closes, and leaves focus on the trigger', async () => {
    const user = userEvent.setup();
    const { calls, items } = spec();
    render(<OverflowMenu label={LABEL} items={items} />);

    await user.click(trigger());
    await user.click(within(menu()).getByRole('menuitem', { name: 'Archive strategy' }));

    expect(calls.archive).toBe(1);
    expect(calls.deploy).toBe(0);
    expect(screen.queryByRole('menu')).toBeNull();
    // Where a `ConfirmDialog` opened by the handler returns focus when it is cancelled.
    expect(document.activeElement).toBe(trigger());
  });

  it('activates an entry with the keyboard, not only with a pointer', async () => {
    const user = userEvent.setup();
    const { calls, items } = spec();
    render(<OverflowMenu label={LABEL} items={items} />);

    await user.click(trigger());
    await user.keyboard('{ArrowDown}{Enter}');

    expect(calls.deploy).toBe(1);
  });
});

describe('OverflowMenu: Requirement 15.3 — a disabled entry says why', () => {
  it('throws in development when a disabled entry carries no reason', () => {
    const { items } = spec({
      extra: [{ id: 'edit', label: 'Edit', disabled: true, onSelect: () => {} }],
    });
    expect(() => render(<OverflowMenu label={LABEL} items={items} />))
      .toThrow(/disabledReason/);
  });

  it('keeps a disabled entry focusable and its reason readable, and refuses to act', async () => {
    const user = userEvent.setup();
    const { calls, items } = spec();
    const busy = items.map((item) => ({
      ...item,
      disabled: true,
      disabledReason: 'An action on this strategy is still in progress.',
    }));
    render(<OverflowMenu label={LABEL} items={busy} />);

    await user.click(trigger());
    const entry = within(menu()).getByRole('menuitem', { name: /Archive strategy/ });

    // `aria-disabled`, not `disabled`: the entry stays in the tab order so the reason can be
    // reached at all.
    expect(entry.getAttribute('aria-disabled')).toBe('true');
    expect(entry.hasAttribute('disabled')).toBe(false);
    expect(document.getElementById(entry.getAttribute('aria-describedby')).textContent)
      .toMatch(/still in progress/);

    await user.click(entry);
    expect(calls.archive).toBe(0);
  });
});

describe('nextMenuIndex', () => {
  it('maps the vertical keys onto wrapping movement and ignores everything else', () => {
    expect(nextMenuIndex('ArrowDown', 0, 3)).toBe(1);
    expect(nextMenuIndex('ArrowDown', 2, 3)).toBe(0);
    expect(nextMenuIndex('ArrowUp', 0, 3)).toBe(2);
    expect(nextMenuIndex('Home', 2, 3)).toBe(0);
    expect(nextMenuIndex('End', 0, 3)).toBe(2);
    expect(nextMenuIndex('ArrowRight', 0, 3)).toBe(null);
    expect(nextMenuIndex('Enter', 0, 3)).toBe(null);
  });

  it('is total over a garbage count or index', () => {
    expect(nextMenuIndex('ArrowDown', 0, 0)).toBe(null);
    expect(nextMenuIndex('ArrowDown', 99, 3)).toBe(1);
    expect(nextMenuIndex('ArrowDown', -4, 3)).toBe(1);
    expect(nextMenuIndex('constructor', 0, 3)).toBe(null);
  });
});
