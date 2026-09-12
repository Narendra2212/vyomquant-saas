/**
 * `ds/SectionHeader` — vyomquant-ui-redesign task 6.23.
 * Requirements 1.2, 18.4. design.md §5.1, §5.3.
 *
 * Renames and retokens `PanelTitle`, whose fixed `<h3>` is what produced heading outlines
 * with a level skipped; the level is the caller's here.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import { SectionHeader } from '../../../src/components/ds/SectionHeader';

const root = () => document.querySelector('[data-ds="section-header"]');

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe('SectionHeader: the heading level is the caller\'s', () => {
  it('renders an h2 at the section token by default', () => {
    render(<SectionHeader title="Open positions" />);
    const heading = screen.getByRole('heading', { level: 2 });
    expect(heading.tagName).toBe('H2');
    expect(heading.className).toContain('text-section');
    expect(root().getAttribute('data-ds-level')).toBe('2');
  });

  it('renders an h3 at the title token for level 3', () => {
    render(<SectionHeader title="Session controls" level={3} />);
    const heading = screen.getByRole('heading', { level: 3 });
    expect(heading.tagName).toBe('H3');
    expect(heading.className).toContain('text-title');
  });

  it('throws on any other level and falls back to a heading in production', () => {
    expect(() => render(<SectionHeader title="Positions" level={4} />)).toThrow(
      /`level` must be 2 or 3/,
    );

    vi.stubEnv('DEV', false);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<SectionHeader title="Positions" level={4} />);
    // A possibly-wrong depth still navigates; a non-heading does not appear at all.
    expect(screen.getByRole('heading', { level: 2 })).toBeTruthy();
  });
});

describe('SectionHeader: content', () => {
  it('requires a title', () => {
    expect(() => render(<SectionHeader />)).toThrow(/`title` is required/);
    expect(() => render(<SectionHeader title="" />)).toThrow(/`title` is required/);
  });

  it('renders the subtitle beneath the title, in the secondary token', () => {
    render(<SectionHeader title="Equity curve" subtitle="Drawn from persisted snapshots" />);
    const subtitle = screen.getByText('Drawn from persisted snapshots');
    expect(subtitle.className).toContain('text-small');
    expect(subtitle.className).toContain('text-content-secondary');
    // `PanelTitle`'s 10px monospace subtitle is retired: prose is not mono.
    expect(subtitle.className).not.toContain('font-mono');
  });

  it('renders nothing for an absent subtitle or right slot', () => {
    render(<SectionHeader title="Drawdown" />);
    expect(root().querySelector('p')).toBeNull();
    expect(root().children.length).toBe(1);
  });

  it('renders the right slot at the far end', () => {
    render(<SectionHeader title="Profit and loss" right={<span>Simulated</span>} />);
    expect(screen.getByText('Simulated')).toBeTruthy();
    expect(root().children.length).toBe(2);
  });

  it('appends a caller className without replacing its own layout', () => {
    render(<SectionHeader title="Trades" className="mt-6" />);
    expect(root().className).toContain('mt-6');
    expect(root().className).toContain('justify-between');
  });
});
