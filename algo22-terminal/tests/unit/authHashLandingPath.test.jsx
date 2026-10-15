/**
 * FE-01 -- a URL-hash `access_token` is no longer adopted as the session on an
 * arbitrary page.
 *
 * THE DEFECT
 *   `getEffectiveToken()` (read by both `AuthGuard` and `GuestGuard`, so on every
 *   protected and every guest-only route) checked `window.location.hash` for
 *   `access_token=` on ANY path and, if the token was not demonstrably expired, silently
 *   adopted it as the caller's session via `sessionStorage.setItem`. A non-expired token
 *   proves only that Supabase signed it for SOMEONE -- not that it was issued to this
 *   browser, and not that this page load is a legitimate OAuth/magic-link redirect. An
 *   attacker who owns a valid Supabase account can craft
 *   `https://<app>/app/strategies/xyz#access_token=<their own token>` and a victim who
 *   clicks it has their browser log in AS THE ATTACKER'S ACCOUNT -- a login CSRF, not an
 *   open redirect. `PasswordRecoveryHandler`'s own `access_token=` branch (not the
 *   `type=recovery` one) had the identical defect, unconditionally.
 *
 * THE FIX
 *   Both call sites now require `window.location.pathname` to be one of the paths
 *   Supabase is actually ever told to redirect to (`AUTH_REDIRECT_PATH`, `/signin`,
 *   `/signup`, `/` -- see `config.js::getAuthRedirectUrl`, used by every OTP/sign-up/
 *   resend/OAuth call site in `AuthPage.jsx`). A hash arriving anywhere else is no longer
 *   honoured. This narrows the attack to the one path an attacker must target instead of
 *   any page in the app; it does not make the underlying implicit OAuth flow itself
 *   CSRF-proof (PKCE would, and is not attempted here -- see the code comment on
 *   `getEffectiveToken` for why). The `type=recovery` branch is UNCHANGED beyond an
 *   added expiry check, since this app never calls `resetPasswordForEmail` with an
 *   explicit `redirectTo` and so cannot assert which path a real recovery email lands on.
 *
 * WHAT THIS FILE PROVES
 *   * the allowlist accepts exactly the dashboard/signin/signup/root paths and nothing
 *     else -- in particular, a strategy/portfolio/settings-style path is rejected;
 *   * the allowlist is built from the same `AUTH_REDIRECT_PATH` constant `AuthPage.jsx`
 *     feeds to Supabase, so the two cannot drift independently;
 *   * a direct exercise of `getEffectiveToken()`: a hash token on a disallowed path is
 *     never stored and is not returned; the identical hash on `AUTH_REDIRECT_PATH` IS
 *     adopted -- proving this is a narrowing, not a removal, of the feature.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { _isLegitimateAuthHashLandingPath, getEffectiveToken } from '../../src/App';
import { AUTH_REDIRECT_PATH } from '../../src/config';

const setLocation = (pathname, hash = '') => {
  vi.stubGlobal('location', { ...window.location, pathname, hash });
};

describe('FE-01: the auth hash-landing allowlist', () => {
  it('accepts exactly the dashboard/signin/signup/root paths', () => {
    expect(_isLegitimateAuthHashLandingPath(AUTH_REDIRECT_PATH)).toBe(true);
    expect(_isLegitimateAuthHashLandingPath('/signin')).toBe(true);
    expect(_isLegitimateAuthHashLandingPath('/signup')).toBe(true);
    expect(_isLegitimateAuthHashLandingPath('/')).toBe(true);
  });

  it('rejects an arbitrary in-app page -- the attack this finding is about', () => {
    expect(_isLegitimateAuthHashLandingPath('/app/strategies/xyz')).toBe(false);
    expect(_isLegitimateAuthHashLandingPath('/app/portfolio')).toBe(false);
    expect(_isLegitimateAuthHashLandingPath('/app/settings')).toBe(false);
  });
});

describe('FE-01: getEffectiveToken no longer adopts a hash token off the allowlist', () => {
  const REAL_TOKEN = () => {
    const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
    const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600, sub: 'attacker' }));
    return `${header}.${payload}.signature`;
  };

  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it('ignores a hash access_token on a disallowed path, and stores nothing', () => {
    setLocation('/app/strategies/xyz', `#access_token=${REAL_TOKEN()}`);

    const token = getEffectiveToken();

    expect(token).toBeNull();
    expect(sessionStorage.getItem('token')).toBeNull();
  });

  it('still adopts the identical hash token on the real redirect landing path', () => {
    const jwt = REAL_TOKEN();
    setLocation(AUTH_REDIRECT_PATH, `#access_token=${jwt}`);

    const token = getEffectiveToken();

    expect(token).toBe(jwt);
    expect(sessionStorage.getItem('token')).toBe(jwt);
  });
});
