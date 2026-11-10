/**
 * NAV-01: a link opened in a new tab landed on the dashboard instead of the page it named.
 *
 * The tab has a persisted Supabase session but no per-tab `sessionStorage` token yet, so
 * `AuthGuard` sends it to `/signin`; by the time `GuestGuard` renders, the session listener has
 * restored the token, and the user was sent to `/app/dashboard`. The requested path now travels
 * through the redirect, and only a same-app `/app/...` path is honoured.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';

import { AuthGuard, GuestGuard, safeReturnPath } from '../../src/App';

const TOKEN = () => {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600, sub: 'u' }));
  return `${header}.${payload}.signature`;
};

function SigninProbe() {
  const location = useLocation();
  return <div>signin form, from={String(location.state?.from)}</div>;
}

const routes = (
  <Routes>
    <Route element={<GuestGuard />}>
      <Route path="/signin" element={<SigninProbe />} />
    </Route>
    <Route element={<AuthGuard />}>
      <Route path="/app/paper-trading" element={<div>paper trading page</div>} />
    </Route>
    <Route path="/app/dashboard" element={<div>dashboard page</div>} />
  </Routes>
);

describe('NAV-01: return to the requested page after the sign-in hop', () => {
  beforeEach(() => sessionStorage.clear());
  afterEach(() => sessionStorage.clear());

  it('AuthGuard carries the requested path to /signin', () => {
    render(<MemoryRouter initialEntries={['/app/paper-trading?session=abc']}>{routes}</MemoryRouter>);

    expect(screen.getByText('signin form, from=/app/paper-trading?session=abc')).toBeDefined();
  });

  it('GuestGuard sends a now-signed-in user back to that page, not the dashboard', () => {
    sessionStorage.setItem('token', TOKEN());
    render(
      <MemoryRouter initialEntries={[{ pathname: '/signin', state: { from: '/app/paper-trading' } }]}>
        {routes}
      </MemoryRouter>,
    );

    expect(screen.getByText('paper trading page')).toBeDefined();
  });

  it('with nothing requested, still goes to the dashboard', () => {
    sessionStorage.setItem('token', TOKEN());
    render(<MemoryRouter initialEntries={['/signin']}>{routes}</MemoryRouter>);

    expect(screen.getByText('dashboard page')).toBeDefined();
  });

  it('refuses anything that is not one of this app\'s own pages (no open redirect)', () => {
    for (const bad of ['https://evil.example/app/x', '//evil.example/app/x', '/signin', '/',
      '/app/\\evil', null, 42, '/application']) {
      expect(safeReturnPath(bad)).toBeNull();
    }
    expect(safeReturnPath('/app/strategies/abc?tab=1')).toBe('/app/strategies/abc?tab=1');
  });
});
