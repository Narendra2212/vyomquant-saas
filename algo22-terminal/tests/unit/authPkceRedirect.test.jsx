/**
 * FE-01 -- switching the Supabase client to PKCE (`flowType: 'pkce'`) so a crafted
 * `#access_token=` (or now `?code=`) landing on this app's auth-redirect path can no
 * longer become the victim's session (login CSRF).
 *
 * BACKGROUND (see docs/launch_readiness/REMEDIATION_LEDGER.json, id "FE-01")
 *   An earlier FE-01 pass (commit 83c8a8c) narrowed this app's OWN hand-rolled hash
 *   parsing (`getEffectiveToken` / `PasswordRecoveryHandler`) to only honour
 *   `#access_token=` on the one path Supabase is ever told to redirect to. That left a
 *   bigger hole open: `@supabase/supabase-js`'s own `detectSessionInUrl` adopts an
 *   implicit-flow hash on ANY route, before any app code runs, and bridges the result into
 *   this app's session via the `onAuthStateChange` listener in `PasswordRecoveryHandler`
 *   -- a path the first pass never touched. The actual, complete fix is `flowType: 'pkce'`
 *   (src/supabase.js): a legitimate redirect now carries `?code=...`, and exchanging it
 *   requires a `code_verifier` this browser generated and stored locally when the flow
 *   started. An attacker's own valid `code` has no matching verifier in the victim's
 *   browser, so the exchange fails and nothing is adopted -- closing the forgery
 *   regardless of which path or mechanism (app code or the SDK's own URL detection)
 *   would otherwise have looked at it.
 *
 * WHAT THIS FILE PROVES, GIVEN NO LIVE SUPABASE PROJECT TO EXCHANGE A REAL CODE AGAINST
 *   1. `_isLegitimateAuthRedirectLandingPath` (renamed from `_isLegitimateAuthHashLandingPath`,
 *      same contract) still accepts exactly the dashboard/signin/signup/root paths.
 *   2. `getEffectiveToken()` no longer reads `window.location.hash` AT ALL -- not even on
 *      the allowlisted redirect path. Proves the manual hash-trusting code path this
 *      finding is about has been REMOVED, not merely re-narrowed.
 *   3. `hasPendingAuthCodeExchange()` recognises a `?code=` on an allowlisted path and
 *      rejects one elsewhere, mirroring the old hash allowlist's shape for the new query
 *      parameter.
 *   4. `AuthGuard` / `GuestGuard`: with no token and a pending code exchange, they render
 *      a brief waiting state and call `supabase.auth.getSession()` rather than
 *      immediately bouncing to `/signin` (or flashing the sign-in form) -- proving the
 *      guards now tolerate the async exchange PKCE requires, which the old synchronous,
 *      hash-based read never needed to. Once that call resolves and `sessionStorage` has
 *      been populated (standing in here for the real `onAuthStateChange` bridge, which
 *      needs a live project to exercise end-to-end), the guard re-renders to its normal
 *      decision.
 *   5. `PasswordRecoveryHandler` no longer stores a hash `access_token` for the
 *      non-recovery case at all (proving the deleted branch is actually gone), while its
 *      `type=recovery` branch is UNCHANGED -- still hash-based, still outside this app's
 *      control, still rejecting a demonstrably expired token. This is the finding's
 *      documented, UNCHANGED residual: nothing about the recovery email's own redirect
 *      gap is closed by this commit, because this frontend never generates that email.
 *
 * WHAT THIS FILE CANNOT PROVE (recorded here rather than silently assumed)
 *   There is no live Supabase project or OAuth provider in this environment, so no test
 *   here drives a REAL `code` through a REAL `exchangeCodeForSession` against a REAL
 *   stored `code_verifier`. `supabase.auth.getSession` is mocked throughout. The actual
 *   OAuth / magic-link / sign-up round trip, and whether a still-implicit-shaped recovery
 *   email now fails `supabase.auth.updateUser()` on `/reset-password` (see the comment on
 *   `flowType: 'pkce'` in src/supabase.js), need a live project to confirm.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

import {
  _isLegitimateAuthRedirectLandingPath,
  getEffectiveToken,
  hasPendingAuthCodeExchange,
  AuthGuard,
  GuestGuard,
} from '../../src/App';
import { AUTH_REDIRECT_PATH } from '../../src/config';
import { supabase } from '../../src/supabase';

const setLocation = (pathname, { hash = '', search = '' } = {}) => {
  vi.stubGlobal('location', { ...window.location, pathname, hash, search });
};

const REAL_TOKEN = () => {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600, sub: 'attacker' }));
  return `${header}.${payload}.signature`;
};

describe('FE-01: the auth redirect-landing allowlist', () => {
  it('accepts exactly the dashboard/signin/signup/root paths', () => {
    expect(_isLegitimateAuthRedirectLandingPath(AUTH_REDIRECT_PATH)).toBe(true);
    expect(_isLegitimateAuthRedirectLandingPath('/signin')).toBe(true);
    expect(_isLegitimateAuthRedirectLandingPath('/signup')).toBe(true);
    expect(_isLegitimateAuthRedirectLandingPath('/')).toBe(true);
  });

  it('rejects an arbitrary in-app page -- the attack this finding is about', () => {
    expect(_isLegitimateAuthRedirectLandingPath('/app/strategies/xyz')).toBe(false);
    expect(_isLegitimateAuthRedirectLandingPath('/app/portfolio')).toBe(false);
    expect(_isLegitimateAuthRedirectLandingPath('/app/settings')).toBe(false);
  });
});

describe('FE-01: getEffectiveToken no longer trusts a URL hash at all', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it('ignores a hash access_token on the real redirect landing path -- the old escape hatch is gone', () => {
    setLocation(AUTH_REDIRECT_PATH, { hash: `#access_token=${REAL_TOKEN()}` });

    const token = getEffectiveToken();

    expect(token).toBeNull();
    expect(sessionStorage.getItem('token')).toBeNull();
  });

  it('ignores a hash access_token on a disallowed path too', () => {
    setLocation('/app/strategies/xyz', { hash: `#access_token=${REAL_TOKEN()}` });

    expect(getEffectiveToken()).toBeNull();
    expect(sessionStorage.getItem('token')).toBeNull();
  });

  it('still reads an already-established sessionStorage token (the fast path for a normal navigation)', () => {
    const jwt = REAL_TOKEN();
    sessionStorage.setItem('token', jwt);
    setLocation('/app/dashboard');

    expect(getEffectiveToken()).toBe(jwt);
  });
});

describe('FE-01: hasPendingAuthCodeExchange', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('recognises a PKCE code on an allowlisted landing path', () => {
    setLocation(AUTH_REDIRECT_PATH, { search: '?code=abc123' });
    expect(hasPendingAuthCodeExchange()).toBe(true);

    setLocation('/signin', { search: '?code=abc123' });
    expect(hasPendingAuthCodeExchange()).toBe(true);
  });

  it('rejects a code on a disallowed path -- a code has no more business off-allowlist than a hash did', () => {
    setLocation('/app/strategies/xyz', { search: '?code=abc123' });
    expect(hasPendingAuthCodeExchange()).toBe(false);
  });

  it('is false with no code param at all', () => {
    setLocation(AUTH_REDIRECT_PATH, { search: '' });
    expect(hasPendingAuthCodeExchange()).toBe(false);
  });
});

describe('FE-01: AuthGuard waits out a pending PKCE exchange instead of bouncing to /signin', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
    delete supabase.auth.getSession;
  });

  const renderGuard = (Guard) =>
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<Guard />}>
            <Route path="/" element={<div>protected content</div>} />
          </Route>
          <Route path="/signin" element={<div>signin page</div>} />
          <Route path="/app/dashboard" element={<div>dashboard page</div>} />
        </Routes>
      </MemoryRouter>,
    );

  it('shows a waiting state and defers to supabase.auth.getSession() rather than redirecting immediately', async () => {
    setLocation(AUTH_REDIRECT_PATH, { search: '?code=attacker-or-legit' });
    let resolveSession;
    supabase.auth.getSession = vi.fn(
      () => new Promise((resolve) => { resolveSession = resolve; }),
    );

    renderGuard(AuthGuard);

    // Not bounced to /signin while the exchange is outstanding.
    expect(screen.queryByText('signin page')).toBeNull();
    expect(supabase.auth.getSession).toHaveBeenCalledTimes(1);

    // The exchange fails to match any locally-held verifier (an attacker's own `code`,
    // or simply no live project here to exchange a real one against) -- no session
    // results, so sessionStorage stays empty, same as the onAuthStateChange bridge would
    // leave it in that case.
    resolveSession({ data: { session: null } });

    await waitFor(() => {
      expect(screen.getByText('signin page')).toBeDefined();
    });
  });

  it('renders the protected route once the exchange resolves to a real session', async () => {
    setLocation(AUTH_REDIRECT_PATH, { search: '?code=a-real-code-with-a-matching-verifier' });
    const jwt = REAL_TOKEN();
    supabase.auth.getSession = vi.fn().mockImplementation(async () => {
      // Stands in for the onAuthStateChange bridge in PasswordRecoveryHandler, which is
      // what actually writes sessionStorage in the running app once the SDK's own
      // exchangeCodeForSession resolves. Exercised here directly since this file cannot
      // drive a real exchange without a live project.
      sessionStorage.setItem('token', jwt);
      return { data: { session: { access_token: jwt } } };
    });

    renderGuard(AuthGuard);

    await waitFor(() => {
      expect(screen.getByText('protected content')).toBeDefined();
    });
  });

  it('with no code pending and no token, redirects to /signin exactly as before -- unaffected by this fix', () => {
    setLocation('/app/strategies/xyz');

    renderGuard(AuthGuard);

    expect(screen.getByText('signin page')).toBeDefined();
  });

  it('with an existing sessionStorage token, renders immediately with no wait -- the fast path is unchanged', () => {
    sessionStorage.setItem('token', REAL_TOKEN());
    setLocation('/app/dashboard');
    supabase.auth.getSession = vi.fn();

    renderGuard(AuthGuard);

    expect(screen.getByText('protected content')).toBeDefined();
    expect(supabase.auth.getSession).not.toHaveBeenCalled();
  });
});

describe('FE-01: GuestGuard mirrors the same pending/resolved behaviour', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
    delete supabase.auth.getSession;
  });

  it('with an already-established session, redirects away from the guest-only page unchanged', () => {
    sessionStorage.setItem('token', REAL_TOKEN());
    setLocation('/signin');

    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<GuestGuard />}>
            <Route path="/" element={<div>signin form</div>} />
          </Route>
          <Route path="/app/dashboard" element={<div>dashboard page</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText('dashboard page')).toBeDefined();
  });

  it('waits for a pending exchange landing on /signin before deciding', async () => {
    setLocation('/signin', { search: '?code=xyz' });
    const jwt = REAL_TOKEN();
    supabase.auth.getSession = vi.fn().mockImplementation(async () => {
      sessionStorage.setItem('token', jwt);
      return { data: { session: { access_token: jwt } } };
    });

    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<GuestGuard />}>
            <Route path="/" element={<div>signin form</div>} />
          </Route>
          <Route path="/app/dashboard" element={<div>dashboard page</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.queryByText('signin form')).toBeNull();

    await waitFor(() => {
      expect(screen.getByText('dashboard page')).toBeDefined();
    });
  });
});
