/**
 * `ds/Tabs` — vyomquant-ui-redesign task 6.23.
 * Requirements 1.2, 6.4, 18.1, 18.4. design.md §5.2, §7.4.
 *
 * The load-bearing blocks are the keyboard model and the roving tabindex. A tab strip
 * built out of plain buttons passes a naive "can I click it" test and still costs a
 * keyboard user one tab stop per tab, which is the thing this component exists to fix —
 * so the tab order is asserted directly, not inferred from the arrow keys working.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { nextTabIndex, Tabs } from '../../../src/components/ds/Tabs';

/** §7.4's three Backtester tabs, which are this component's first consumer. */
const BACKTEST_TABS = Object.freeze([
  { id: 'trades', label: 'Trades', content: <p>Trade list</p> },
  { id: 'monthly', label: 'Monthly returns', content: <p>Monthly table</p> },
  { id: 'stats', label: 'Extended statistics', content: <p>Extended stats</p> },
]);

const tabs = () => screen.getAllByRole('tab');
const selectedTab = () => screen.getByRole('tab', { selected: true });

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe('nextTabIndex: the keyboard model as arithmetic', () => {
  it('moves one step per arrow press and wraps at both ends', () => {
    expect(nextTabIndex('ArrowRight', 0, 3)).toBe(1);
    expect(nextTabIndex('ArrowRight', 2, 3)).toBe(0);
    expect(nextTabIndex('ArrowLeft', 1, 3)).toBe(0);
    expect(nextTabIndex('ArrowLeft', 0, 3)).toBe(2);
  });

  it('jumps to the ends for Home and End', () => {
    expect(nextTabIndex('Home', 2, 3)).toBe(0);
    expect(nextTabIndex('End', 0, 3)).toBe(2);
  });

  it('returns null for every key it does not own, so the event is left alone', () => {
    ['Tab', 'Enter', ' ', 'ArrowUp', 'ArrowDown', 'a', 'Escape', ''].forEach((key) => {
      expect(nextTabIndex(key, 0, 3)).toBeNull();
    });
  });

  it('is total over inputs no strip can produce', () => {
    expect(nextTabIndex('ArrowRight', 0, 0)).toBeNull();
    expect(nextTabIndex('ArrowRight', 0, -1)).toBeNull();
    expect(nextTabIndex('ArrowRight', 0, 1.5)).toBeNull();
    // An index outside the strip is treated as index 0 rather than producing NaN.
    expect(nextTabIndex('ArrowRight', 99, 3)).toBe(1);
    expect(nextTabIndex('ArrowLeft', -4, 3)).toBe(2);
  });
});

describe('Tabs: the ARIA wiring', () => {
  it('names the tablist and pairs every tab with its panel in both directions', () => {
    render(<Tabs label="Backtest details" items={BACKTEST_TABS} />);

    expect(screen.getByRole('tablist', { name: 'Backtest details' })).toBeTruthy();

    const all = tabs();
    expect(all.length).toBe(3);
    all.forEach((tab) => {
      const panelId = tab.getAttribute('aria-controls');
      const panel = document.getElementById(panelId);
      // Every reference resolves. A tab pointing at an id that is not in the document is
      // worse than no reference, because assistive technology follows it.
      expect(panel).toBeTruthy();
      expect(panel.getAttribute('role')).toBe('tabpanel');
      expect(panel.getAttribute('aria-labelledby')).toBe(tab.getAttribute('id'));
    });
  });

  it('marks exactly one tab selected and hides the other panels', () => {
    render(<Tabs label="Backtest details" items={BACKTEST_TABS} />);

    expect(tabs().filter((tab) => tab.getAttribute('aria-selected') === 'true').length).toBe(1);
    expect(selectedTab().textContent).toBe('Trades');

    // `hidden` computes to `display: none`, so an inactive panel is out of the
    // accessibility tree — which is why `getByRole` finds one panel and not three.
    expect(screen.getAllByRole('tabpanel').length).toBe(1);
    expect(document.querySelectorAll('[role="tabpanel"]').length).toBe(3);
  });

  it('makes the panel focusable so a panel with no controls is still reachable', () => {
    render(<Tabs label="Backtest details" items={BACKTEST_TABS} />);
    expect(screen.getByRole('tabpanel').getAttribute('tabindex')).toBe('0');
  });

  it('throws in development without a label or without usable items', () => {
    expect(() => render(<Tabs items={BACKTEST_TABS} />)).toThrow(/`label` is required/);
    expect(() => render(<Tabs label="Details" items={[]} />)).toThrow(/non-empty array/);
    expect(() => render(<Tabs label="Details" items={[{ label: 'No id' }]} />)).toThrow(
      /non-empty array/,
    );
  });
});

describe('Tabs: the roving tabindex', () => {
  it('puts only the selected tab in the tab order', () => {
    render(<Tabs label="Backtest details" items={BACKTEST_TABS} />);
    expect(tabs().map((tab) => tab.getAttribute('tabindex'))).toEqual(['0', '-1', '-1']);
  });

  it('moves the single tab stop with the selection', () => {
    render(<Tabs label="Backtest details" items={BACKTEST_TABS} defaultValue="stats" />);
    expect(tabs().map((tab) => tab.getAttribute('tabindex'))).toEqual(['-1', '-1', '0']);

    fireEvent.click(screen.getByRole('tab', { name: 'Monthly returns' }));
    expect(tabs().map((tab) => tab.getAttribute('tabindex'))).toEqual(['-1', '0', '-1']);
  });
});

describe('Tabs: arrow-key navigation', () => {
  const arrow = (key) => fireEvent.keyDown(selectedTab(), { key });

  it('moves selection and focus with Left and Right, wrapping at both ends', () => {
    render(<Tabs label="Backtest details" items={BACKTEST_TABS} />);

    arrow('ArrowRight');
    expect(selectedTab().textContent).toBe('Monthly returns');
    expect(document.activeElement).toBe(selectedTab());

    arrow('ArrowRight');
    expect(selectedTab().textContent).toBe('Extended statistics');

    // Off the end and round to the first.
    arrow('ArrowRight');
    expect(selectedTab().textContent).toBe('Trades');

    // And backwards off the start.
    arrow('ArrowLeft');
    expect(selectedTab().textContent).toBe('Extended statistics');
    expect(document.activeElement).toBe(selectedTab());
  });

  it('jumps to the first and last tab with Home and End', () => {
    render(<Tabs label="Backtest details" items={BACKTEST_TABS} />);

    arrow('End');
    expect(selectedTab().textContent).toBe('Extended statistics');

    arrow('Home');
    expect(selectedTab().textContent).toBe('Trades');
  });

  it('swaps which panel is exposed as selection follows focus', () => {
    render(<Tabs label="Backtest details" items={BACKTEST_TABS} />);
    expect(screen.getByRole('tabpanel').textContent).toBe('Trade list');

    arrow('ArrowRight');
    expect(screen.getByRole('tabpanel').textContent).toBe('Monthly table');
    expect(screen.getAllByRole('tabpanel').length).toBe(1);
  });

  it('leaves keys it does not own alone', () => {
    render(<Tabs label="Backtest details" items={BACKTEST_TABS} />);
    const event = fireEvent.keyDown(selectedTab(), { key: 'ArrowDown' });
    // `fireEvent` returns false when a handler called `preventDefault`.
    expect(event).toBe(true);
    expect(selectedTab().textContent).toBe('Trades');
  });
});

describe('Tabs: controlled and uncontrolled', () => {
  it('holds its own selection when uncontrolled, opening on defaultValue', () => {
    render(<Tabs label="Backtest details" items={BACKTEST_TABS} defaultValue="monthly" />);
    expect(selectedTab().textContent).toBe('Monthly returns');

    fireEvent.click(screen.getByRole('tab', { name: 'Extended statistics' }));
    expect(selectedTab().textContent).toBe('Extended statistics');
  });

  it('ignores a defaultValue that names no tab and opens the first', () => {
    render(<Tabs label="Backtest details" items={BACKTEST_TABS} defaultValue="nope" />);
    expect(selectedTab().textContent).toBe('Trades');
  });

  it('defers to the caller when controlled, and does not move on its own', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <Tabs label="Backtest details" items={BACKTEST_TABS} value="monthly" onChange={onChange} />,
    );
    expect(selectedTab().textContent).toBe('Monthly returns');

    fireEvent.click(screen.getByRole('tab', { name: 'Trades' }));
    expect(onChange).toHaveBeenCalledWith('trades', 0);
    // The caller has not re-rendered with a new value, so the selection has not moved.
    expect(selectedTab().textContent).toBe('Monthly returns');

    rerender(
      <Tabs label="Backtest details" items={BACKTEST_TABS} value="trades" onChange={onChange} />,
    );
    expect(selectedTab().textContent).toBe('Trades');
  });

  it('reports an arrow-key move through onChange, since selection follows focus', () => {
    const onChange = vi.fn();
    render(
      <Tabs label="Backtest details" items={BACKTEST_TABS} value="trades" onChange={onChange} />,
    );
    fireEvent.keyDown(selectedTab(), { key: 'End' });
    expect(onChange).toHaveBeenCalledWith('stats', 2);
  });

  it('throws in development when a controlled value names no tab', () => {
    expect(() =>
      render(<Tabs label="Backtest details" items={BACKTEST_TABS} value="deleted" />),
    ).toThrow(/not one of the declared ids/);
  });

  it('falls back to the first tab in production rather than rendering no selection', () => {
    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<Tabs label="Backtest details" items={BACKTEST_TABS} value="deleted" />);
    expect(selectedTab().textContent).toBe('Trades');
    expect(spy).toHaveBeenCalled();
  });
});

describe('Tabs: items that cannot be paired are dropped', () => {
  it('drops a duplicate id rather than emitting two elements with one DOM id', () => {
    render(
      <Tabs
        label="Details"
        items={[
          { id: 'trades', label: 'Trades', content: <p>First</p> },
          { id: 'trades', label: 'Trades again', content: <p>Second</p> },
        ]}
      />,
    );
    expect(tabs().length).toBe(1);
    expect(screen.getByRole('tabpanel').textContent).toBe('First');
  });

  it('drops an id carrying whitespace, which aria-controls would split', () => {
    render(
      <Tabs
        label="Details"
        items={[
          { id: 'trade list', label: 'Trades' },
          { id: 'monthly', label: 'Monthly returns' },
        ]}
      />,
    );
    expect(tabs().length).toBe(1);
    expect(selectedTab().textContent).toBe('Monthly returns');
  });
});
