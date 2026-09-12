/**
 * `ds/FilterBar` — vyomquant-ui-redesign task 6.14. Requirements 11.2, 11.5.
 *
 * The filter shapes used here are the real ones from `TradeHistory` (four chips),
 * `Strategies` (status + environment) and `SignalTrace` (text, select and date), so
 * the component is exercised against what tasks 15.1, 17.1 and 21.4 will hand it.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { COUNT_ATTR, emptyVariantFor, FilterBar, FILTER_KINDS } from '../../../src/components/ds/FilterBar';

/** `TradeHistory`'s four chips, which are `["ALL","BUY","SELL","PROFIT"]` today. */
const SIDE_FILTER = Object.freeze({
  id: 'side',
  label: 'Side',
  kind: 'segmented',
  options: [
    { value: 'ALL', label: 'All' },
    { value: 'BUY', label: 'Buy' },
    { value: 'SELL', label: 'Sell' },
    { value: 'PROFIT', label: 'Profit' },
  ],
});

const BASE = Object.freeze({
  filters: [SIDE_FILTER],
  values: { side: 'ALL' },
  onChange: () => {},
  resultCount: 47,
  totalCount: 312,
});

const mount = (props) => render(<FilterBar {...BASE} {...props} />);

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('FilterBar: Requirement 11.2 — filters and search, both labelled', () => {
  it('declares the four control shapes the in-scope pages need', () => {
    expect(FILTER_KINDS).toEqual(['segmented', 'select', 'text', 'date']);
  });

  it('renders a segmented group as native radios under a visible legend', () => {
    mount();
    const group = screen.getByRole('group', { name: 'Side' });
    expect(group.tagName).toBe('FIELDSET');
    const radios = screen.getAllByRole('radio');
    expect(radios.map((radio) => radio.value)).toEqual(['ALL', 'BUY', 'SELL', 'PROFIT']);
    // One tab stop, single selection, arrow keys — all native.
    expect(radios.filter((radio) => radio.checked).map((radio) => radio.value)).toEqual(['ALL']);
  });

  it('reports a segment change against the filter id and the option value', () => {
    const onChange = vi.fn();
    mount({ onChange });
    fireEvent.click(screen.getByLabelText('Sell'));
    expect(onChange).toHaveBeenCalledWith('side', 'SELL');
  });

  it('labels the search input rather than leaning on its placeholder', () => {
    mount({ search: '', onSearchChange: () => {}, searchPlaceholder: 'Market or strategy' });
    const input = screen.getByLabelText('Search');
    expect(input.getAttribute('type')).toBe('search');
    expect(input.getAttribute('placeholder')).toBe('Market or strategy');
    expect(input.getAttribute('aria-label')).toBeNull();
    expect(screen.queryByLabelText('Market or strategy')).toBeNull();
  });

  it('hands the search text back exactly as typed', () => {
    const onSearchChange = vi.fn();
    mount({ search: '', onSearchChange });
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'BTC/USDT ' } });
    expect(onSearchChange).toHaveBeenCalledWith('BTC/USDT ');
  });

  it('renders no search control when the page does not offer search', () => {
    mount();
    expect(screen.queryByLabelText('Search')).toBeNull();
  });

  it('labels a select and a date filter — SignalTrace\'s placeholder-only inputs', () => {
    const onChange = vi.fn();
    mount({
      filters: [
        { id: 'symbol', label: 'Market', kind: 'text', placeholder: 'BTC/USDT' },
        {
          id: 'decision',
          label: 'Decision',
          kind: 'select',
          options: [
            { value: '', label: 'Any' },
            { value: 'BUY', label: 'Buy' },
          ],
        },
        { id: 'date_from', label: 'From', kind: 'date' },
      ],
      values: { symbol: '', decision: '', date_from: '' },
      onChange,
    });
    expect(screen.getByLabelText('Market').getAttribute('placeholder')).toBe('BTC/USDT');
    expect(screen.getByLabelText('Decision').tagName).toBe('SELECT');
    expect(screen.getByLabelText('From').getAttribute('type')).toBe('date');

    fireEvent.change(screen.getByLabelText('Decision'), { target: { value: 'BUY' } });
    expect(onChange).toHaveBeenCalledWith('decision', 'BUY');
  });

  it('throws for a filter with no label, a bad kind, or a choice with no choices', () => {
    expect(() => mount({ filters: [{ id: 'side', kind: 'segmented', options: [{ value: 'ALL', label: 'All' }] }] }))
      .toThrow(/needs a non-empty `label`/);
    expect(() => mount({ filters: [{ id: 'side', label: 'Side', kind: 'chips', options: [] }] })).toThrow(
      /`kind` must be one of/,
    );
    expect(() => mount({ filters: [{ id: 'side', label: 'Side', kind: 'select' }] })).toThrow(
      /requires a non-empty `options` array/,
    );
  });

  it('renders the page actions it is given', () => {
    mount({ actions: <button type="button">Export CSV</button> });
    expect(screen.getByRole('button', { name: 'Export CSV' })).toBeTruthy();
  });
});

describe('FilterBar: Requirement 11.5 — the count that tells the two empty states apart', () => {
  it('announces the counts in one live region', () => {
    mount();
    const region = screen.getByRole('status');
    expect(region.textContent).toBe('47 of 312');
    expect(region.getAttribute(COUNT_ATTR)).toBe('true');
    expect(document.querySelectorAll('[role="status"]')).toHaveLength(1);
  });

  it('publishes both numbers so a page does not have to parse the sentence', () => {
    mount({ resultCount: 0, totalCount: 312 });
    const region = screen.getByRole('status');
    expect(region.getAttribute('data-result-count')).toBe('0');
    expect(region.getAttribute('data-total-count')).toBe('312');
    expect(region.getAttribute('data-empty-variant')).toBe('no-match');
  });

  it('names what is being counted when the page says so', () => {
    mount({ countNoun: 'trades' });
    expect(screen.getByRole('status').textContent).toBe('47 of 312 trades');
  });

  it('discriminates no-data from no-match, and stays out of the way when rows exist', () => {
    // The one decision, in one place, so three pages cannot answer it three ways.
    expect(emptyVariantFor(0, 0)).toBe('no-data');
    expect(emptyVariantFor(0, 312)).toBe('no-match');
    expect(emptyVariantFor(47, 312)).toBeNull();
    expect(emptyVariantFor(312, 312)).toBeNull();
  });

  it('requires both counts, because without them the distinction is a guess', () => {
    expect(() => mount({ resultCount: undefined })).toThrow(/both required whole counts/);
    expect(() => mount({ totalCount: null })).toThrow(/both required whole counts/);
    expect(() => mount({ resultCount: 1.5 })).toThrow(/both required whole counts/);
    expect(() => mount({ resultCount: -1 })).toThrow(/both required whole counts/);
  });

  it('refuses a result count larger than the total', () => {
    expect(() => mount({ resultCount: 400, totalCount: 312 })).toThrow(/cannot exceed `totalCount`/);
  });

  it('still renders the bar in production when the counts are wrong', () => {
    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mount({ resultCount: undefined, totalCount: undefined });
    expect(screen.getAllByRole('radio')).toHaveLength(4);
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });
});
