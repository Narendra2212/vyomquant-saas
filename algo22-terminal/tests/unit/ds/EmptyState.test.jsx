/**
 * `ds/EmptyState` — vyomquant-ui-redesign task 6.1. Requirements 14.1, 11.5.
 */

import { Layers } from 'lucide-react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import { EmptyState, EMPTY_VARIANTS, REQUIRED_EMPTY_FIELDS } from '../../../src/components/ds/EmptyState';

const COMPLETE = Object.freeze({
  icon: Layers,
  headline: 'No strategies yet',
  body: 'Strategies are what place orders for you. Nothing trades until one is deployed.',
  action: { label: 'Create a strategy', to: '/app/builder' },
});

const mount = (props) => render(<MemoryRouter><EmptyState {...props} /></MemoryRouter>);

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('EmptyState: all three of Requirement 14.1\'s fields', () => {
  it('renders what is missing, why it matters and the next action', () => {
    mount(COMPLETE);
    expect(screen.getByText('No strategies yet')).toBeTruthy();
    expect(screen.getByText(/Nothing trades until one is deployed/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Create a strategy' })).toBeTruthy();
  });

  it('throws in development for each missing field, naming the one that is missing', () => {
    expect(() => mount({ ...COMPLETE, headline: undefined })).toThrow(/`headline` is required/);
    expect(() => mount({ ...COMPLETE, body: undefined })).toThrow(/`body` is required/);
    expect(() => mount({ ...COMPLETE, action: undefined })).toThrow(/`action` is required/);
    // The three are the list the property test reads.
    expect(REQUIRED_EMPTY_FIELDS).toEqual(['headline', 'body', 'action']);
  });

  it('rejects copy that is present but blank', () => {
    expect(() => mount({ ...COMPLETE, headline: '   ' })).toThrow(/`headline` is required/);
    expect(() => mount({ ...COMPLETE, action: { label: '' } })).toThrow(/`action` is required/);
  });

  it('names the panel in the failure, so the call site is findable', () => {
    expect(() => mount({ ...COMPLETE, body: undefined })).toThrow(/No strategies yet/);
  });

  it('renders the copy it has in production rather than crashing the page', () => {
    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mount({ ...COMPLETE, body: undefined });
    expect(screen.getByText('No strategies yet')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Create a strategy' })).toBeTruthy();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe('EmptyState: Requirement 11.5\'s two cases', () => {
  it('declares exactly the two variants', () => {
    expect(EMPTY_VARIANTS).toEqual(['no-data', 'no-match']);
  });

  it('defaults to no-data and publishes the variant', () => {
    mount(COMPLETE);
    expect(document.querySelector('[data-empty-variant]').getAttribute('data-empty-variant')).toBe('no-data');
  });

  it('requires a clear-filters action for no-match', () => {
    // The rows exist and a filter is hiding them, so "create a strategy" is the wrong
    // next action. Without this check the filtered case tells a trader to make data
    // they already have.
    expect(() => mount({ ...COMPLETE, variant: 'no-match' })).toThrow(/requires `clearFiltersAction`/);
  });

  it('leads with clearing the filter when the filter is what is in the way', () => {
    mount({
      ...COMPLETE,
      variant: 'no-match',
      headline: 'No strategies match these filters',
      clearFiltersAction: { label: 'Clear filters', onClick: () => {} },
    });
    expect(document.querySelector('[data-empty-variant]').getAttribute('data-empty-variant')).toBe('no-match');
    const controls = [...document.querySelectorAll('a, button')];
    expect(controls.map((c) => c.textContent)).toEqual(['Clear filters', 'Create a strategy']);
  });

  it('throws for a variant outside the two', () => {
    expect(() => mount({ ...COMPLETE, variant: 'no-results' })).toThrow(/`variant` must be one of/);
  });
});

describe('EmptyState: calm, static and unannounced', () => {
  it('renders a static icon with no animation and no injected keyframes', () => {
    // The legacy EmptyState bobbed its icon forever via an inline
    // `<style>@keyframes float` and halved its opacity.
    mount(COMPLETE);
    const region = document.querySelector('[data-empty-variant]');
    expect(document.querySelectorAll('style').length).toBe(0);
    const icon = region.querySelector('svg');
    expect(icon.getAttribute('aria-hidden')).toBe('true');
    expect(icon.style.animation).toBe('');
    expect(region.className).not.toContain('animate');
  });

  it('is not a live region — an empty panel is a resting state, not an event', () => {
    mount(COMPLETE);
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(document.querySelector('[aria-live]')).toBeNull();
  });

  it('renders without an icon', () => {
    mount({ ...COMPLETE, icon: undefined });
    expect(document.querySelector('[data-empty-variant] svg')).toBeNull();
    expect(screen.getByText('No strategies yet')).toBeTruthy();
  });

  it('renders an onClick action as a button and a route as a link', () => {
    const onClick = vi.fn();
    mount({ ...COMPLETE, action: { label: 'Connect an exchange', onClick } });
    const button = screen.getByRole('button', { name: 'Connect an exchange' });
    expect(button.getAttribute('type')).toBe('button');
    button.click();
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
