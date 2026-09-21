/**
 * `ds/PageHeader` — vyomquant-ui-redesign task 6.23.
 * Requirements 1.2, 2.2, 18.4. design.md §5.1, §6.2.
 *
 * The load-bearing assertion is the first block: the reserved height does not move for
 * any combination of `subtitle` and `meta`, which is what stops a route change shifting
 * the content region (Requirement 2.2). Property P2 asserts the same invariant across
 * generated route sequences.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import {
  PAGE_HEADER_HEIGHT_PX,
  PageHeader,
} from '../../../src/components/ds/PageHeader';

const header = () => document.querySelector('[data-ds="page-header"]');

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

/** The four content combinations design.md §5.1's "whether or not" clause names. */
const CONTENT_COMBINATIONS = Object.freeze([
  ['neither subtitle nor meta', {}],
  ['a subtitle only', { subtitle: 'Live account · Binance' }],
  ['meta only', { meta: 'Updated 12:04:31 UTC' }],
  ['both a subtitle and meta', { subtitle: 'Live account · Binance', meta: 'Updated 12:04:31' }],
]);

describe('PageHeader: the reserved block is the same height whatever it holds', () => {
  it.each(CONTENT_COMBINATIONS)('reserves 64px with %s', (_name, props) => {
    render(<PageHeader title="Portfolio" {...props} />);
    expect(header().style.height).toBe(`${PAGE_HEADER_HEIGHT_PX}px`);
    expect(PAGE_HEADER_HEIGHT_PX).toBe(64);
  });

  it('reserves the same 64px with a breadcrumb, an environment badge and actions', () => {
    render(
      <MemoryRouter>
        <PageHeader
          title="RSI Reversion"
          subtitle="4h · BTCUSDT"
          environment="LIVE"
          breadcrumb={[{ label: 'Strategies', to: '/app/strategies' }, { label: 'RSI Reversion' }]}
          actions={<button type="button">Deploy</button>}
          meta="Updated 12:04:31 UTC"
        />
      </MemoryRouter>,
    );
    expect(header().style.height).toBe('64px');
  });

  it('counts padding inside the reserved height, not on top of it', () => {
    render(<PageHeader title="Portfolio" />);
    expect(header().style.boxSizing).toBe('border-box');
  });

  it('clips an oversized action cluster rather than growing', () => {
    // The one thing the layout cannot control is a caller's own node, so the height is
    // held by `overflow` as well as by arrangement.
    render(<PageHeader title="Portfolio" actions={<div style={{ height: 200 }} />} />);
    expect(header().style.overflow).toBe('hidden');
    expect(header().style.height).toBe('64px');
  });

  it('cannot have its height undone by a className or a style prop', () => {
    render(
      <PageHeader title="Portfolio" className="h-auto" style={{ height: '200px', marginTop: '8px' }} />,
    );
    expect(header().style.height).toBe('64px');
    // A caller's other style properties still survive — only the geometry is pinned.
    expect(header().style.marginTop).toBe('8px');
    expect(header().className).toContain('h-auto');
  });
});

describe('PageHeader: the title', () => {
  it('renders the page heading as the one h1, at the page type token', () => {
    render(<PageHeader title="Trade History" />);
    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading.textContent).toBe('Trade History');
    expect(heading.tagName).toBe('H1');
    expect(heading.className).toContain('text-page');
    expect(document.querySelectorAll('h1').length).toBe(1);
  });

  it('throws in development when the title is missing', () => {
    expect(() => render(<PageHeader />)).toThrow(/`title` is required/);
    expect(() => render(<PageHeader title="   " />)).toThrow(/`title` is required/);
  });

  it('renders no empty heading in production, and keeps the reserved height', () => {
    vi.stubEnv('DEV', false);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<PageHeader />);
    expect(document.querySelector('h1')).toBeNull();
    expect(header().style.height).toBe('64px');
    expect(spy).toHaveBeenCalled();
  });
});

describe('PageHeader: the breadcrumb', () => {
  const trail = [
    { label: 'Strategies', to: '/app/strategies' },
    { label: 'RSI Reversion' },
  ];

  it('links every entry but the last, which is the current page', () => {
    render(
      <MemoryRouter>
        <PageHeader title="Strategy" breadcrumb={trail} />
      </MemoryRouter>,
    );
    const nav = screen.getByRole('navigation', { name: 'Breadcrumb' });
    expect(nav).toBeTruthy();

    const link = screen.getByRole('link', { name: 'Strategies' });
    expect(link.getAttribute('href')).toBe('/app/strategies');

    const current = screen.getByText('RSI Reversion');
    expect(current.getAttribute('aria-current')).toBe('page');
    expect(current.tagName).not.toBe('A');
  });

  it('never links the last entry even when it carries a route', () => {
    render(
      <MemoryRouter>
        <PageHeader
          title="Strategy"
          breadcrumb={[{ label: 'Strategies', to: '/app/strategies' }, { label: 'RSI', to: '/app/x' }]}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByRole('link', { name: 'RSI' })).toBeNull();
  });

  it('renders outside a router without failing', () => {
    // A primitive that hard-fails without a router cannot be rendered in isolation.
    render(<PageHeader title="Strategy" breadcrumb={trail} />);
    expect(screen.getByRole('link', { name: 'Strategies' }).getAttribute('href')).toBe(
      '/app/strategies',
    );
  });

  it('drops an entry whose label has not arrived rather than inventing one', () => {
    render(
      <MemoryRouter>
        <PageHeader
          title="Strategy"
          breadcrumb={[{ label: 'Strategies', to: '/app/strategies' }, { label: undefined }]}
        />
      </MemoryRouter>,
    );
    expect(screen.getAllByRole('listitem').length).toBe(1);
    // The surviving entry becomes the current page and stops being a link.
    expect(screen.queryByRole('link', { name: 'Strategies' })).toBeNull();
    expect(screen.getByText('Strategies').getAttribute('aria-current')).toBe('page');
  });

  it('renders no navigation region at all when there is no trail', () => {
    render(<PageHeader title="Portfolio" />);
    expect(screen.queryByRole('navigation')).toBeNull();
  });
});

describe('PageHeader: the environment badge', () => {
  it('renders the badge inline for a named environment', () => {
    render(<PageHeader title="Live Trading" environment="LIVE" />);
    const badge = document.querySelector('[data-environment]');
    expect(badge.getAttribute('data-environment')).toBe('LIVE');
    expect(badge.getAttribute('data-environment-variant')).toBe('chip');
  });

  it('distinguishes an omitted environment from a null one', () => {
    // Omitted: this page is not about one environment, so there is nothing to badge.
    const { unmount } = render(<PageHeader title="Marketplace" />);
    expect(document.querySelector('[data-environment]')).toBeNull();
    unmount();

    // `null`: the server did not report one, which is a state and not an absence.
    render(<PageHeader title="Portfolio" environment={null} />);
    expect(document.querySelector('[data-environment]').getAttribute('data-environment')).toBe(
      'UNCONFIRMED',
    );
  });
});

describe('PageHeader: the right-hand cluster', () => {
  it('renders actions and meta', () => {
    render(
      <PageHeader
        title="Portfolio"
        actions={<button type="button">Refresh</button>}
        meta={<span>Updated 12:04:31 UTC</span>}
      />,
    );
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeTruthy();
    expect(screen.getByText('Updated 12:04:31 UTC')).toBeTruthy();
  });
});
