/**
 * Unit tests for `src/components/shell/navigation.js` — task 8.1.
 * Requirements 2.1, 2.3, 2.4.
 *
 * Scope note: the "exactly ten ids, each with an icon and a label" contract is task 8.2's
 * `nav-contract` guard, and the at-most-one-active property over generated pathnames is
 * task 8.3's Property 3. What is here is the by-example half — the specific pathnames
 * that `Sidebar.jsx`'s current `location.pathname === '/app/' + n.id` check gets wrong,
 * every one of them a route `App.jsx` really declares.
 */

import { describe, it, expect } from 'vitest';
import {
  NAV_GROUPS,
  NAV_ENTRIES,
  SECONDARY_ROUTE_TITLES,
  activeNavId,
  activeNavEntry,
  routeTitle,
} from '../../../src/components/shell/navigation';

describe('NAV_GROUPS', () => {
  it('is the four workflow groups in §6.3 order', () => {
    expect(NAV_GROUPS.map((g) => g.id)).toEqual(['monitor', 'build', 'review', 'practise']);
  });

  it('flattens to the ten Requirement 2.1 entries in sidebar order', () => {
    expect(NAV_ENTRIES.map((e) => e.id)).toEqual([
      'dashboard',
      'live-trading',
      'portfolio',
      'strategies',
      'builder',
      'backtest',
      'signal-trace',
      'trades',
      'paper-trading',
      'marketplace',
    ]);
  });

  it('is frozen all the way down, so no render can retarget an entry', () => {
    expect(Object.isFrozen(NAV_GROUPS)).toBe(true);
    for (const group of NAV_GROUPS) {
      expect(Object.isFrozen(group)).toBe(true);
      expect(Object.isFrozen(group.entries)).toBe(true);
      for (const entry of group.entries) {
        expect(Object.isFrozen(entry)).toBe(true);
        expect(Object.isFrozen(entry.matches)).toBe(true);
      }
    }
  });

  it('carries no global regex, which would make matching stateful', () => {
    for (const entry of NAV_ENTRIES) {
      for (const pattern of entry.matches) {
        expect(pattern.global).toBe(false);
      }
    }
  });
});

describe('activeNavId — canonical paths', () => {
  it('returns the owning entry for each of the ten canonical paths', () => {
    for (const entry of NAV_ENTRIES) {
      expect(activeNavId(entry.path)).toBe(entry.id);
    }
  });

  it('is unaffected by a trailing slash', () => {
    for (const entry of NAV_ENTRIES) {
      expect(activeNavId(`${entry.path}/`)).toBe(entry.id);
    }
  });
});

describe('activeNavId — the aliases and child routes broken today', () => {
  it('highlights Backtester on the /app/backtester alias', () => {
    // Both paths are declared in App.jsx and render the same page; the exact-comparison
    // check in Sidebar.jsx highlights nothing on the alias.
    expect(activeNavId('/app/backtest')).toBe('backtest');
    expect(activeNavId('/app/backtester')).toBe('backtest');
  });

  it('highlights Strategies on a strategy detail route', () => {
    expect(activeNavId('/app/strategies/abc-123')).toBe('strategies');
    expect(activeNavId('/app/strategies/abc-123/versions/7')).toBe('strategies');
  });

  it('highlights Signal Trace on a single-signal route', () => {
    expect(activeNavId('/app/signal-trace/sig_0001')).toBe('signal-trace');
  });

  it('matches case-insensitively, as react-router resolves the routes themselves', () => {
    expect(activeNavId('/app/Dashboard')).toBe('dashboard');
    expect(activeNavId('/APP/PAPER-TRADING')).toBe('paper-trading');
  });
});

describe('activeNavId — what must NOT highlight', () => {
  it('returns null for a live route that is deliberately not a sidebar entry', () => {
    // §6.4 moves these to the account menu. Highlighting nothing is the correct answer.
    for (const path of ['/app/billing', '/app/exchange', '/app/risk', '/app/support']) {
      expect(activeNavId(path)).toBeNull();
    }
  });

  it('does not let a segment match a longer word', () => {
    expect(activeNavId('/app/backtesting')).toBeNull();
    expect(activeNavId('/app/strategiesx')).toBeNull();
    expect(activeNavId('/app/dashboards')).toBeNull();
  });

  it('is anchored, so a nav path appearing later in the string does not match', () => {
    expect(activeNavId('/marketplace')).toBeNull();
    expect(activeNavId('/other/app/dashboard')).toBeNull();
    expect(activeNavId('/app')).toBeNull();
  });

  it('returns null rather than throwing on junk input', () => {
    for (const input of ['', '///', 'not a path', undefined, null, 42, {}]) {
      expect(activeNavId(input)).toBeNull();
    }
  });
});

describe('activeNavEntry', () => {
  it('returns the whole entry, icon included, for a child route', () => {
    const entry = activeNavEntry('/app/strategies/abc-123');
    expect(entry.id).toBe('strategies');
    expect(entry.path).toBe('/app/strategies');
    expect(entry.label).toBe('Strategies');
    expect(entry.icon).toBeTruthy();
  });
});

describe('routeTitle', () => {
  it('gives each nav entry its own label', () => {
    for (const entry of NAV_ENTRIES) {
      expect(routeTitle(entry.path)).toBe(entry.label);
    }
  });

  it('resolves a child route to its parent title for the loading header', () => {
    expect(routeTitle('/app/strategies/abc-123')).toBe('Strategies');
    expect(routeTitle('/app/backtester')).toBe('Backtester');
  });

  it('names the deferred routes so their Suspense fallback is not untitled', () => {
    expect(routeTitle('/app/billing')).toBe('Billing & plan');
    expect(routeTitle('/app/exchange')).toBe('Exchange accounts');
    expect(routeTitle('/app/security-logs')).toBe('Security log');
    expect(routeTitle('/app/security-logs/')).toBe('Security log');
  });

  it('returns null for an unknown route instead of inventing a heading', () => {
    expect(routeTitle('/app/nope')).toBeNull();
    expect(routeTitle('/')).toBeNull();
    expect(routeTitle(undefined)).toBeNull();
  });

  it('does not leak inherited Object properties as titles', () => {
    expect(routeTitle('constructor')).toBeNull();
    expect(routeTitle('toString')).toBeNull();
  });

  it('never disagrees with SECONDARY_ROUTE_TITLES about a nav route', () => {
    for (const entry of NAV_ENTRIES) {
      expect(SECONDARY_ROUTE_TITLES[entry.path]).toBeUndefined();
    }
  });
});
