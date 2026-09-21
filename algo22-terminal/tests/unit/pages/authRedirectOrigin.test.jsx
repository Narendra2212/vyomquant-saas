/**
 * @vitest-environment jsdom
 * @vitest-environment-options { "url": "https://app.vyomquant.in/signin" }
 */

/**
 * tests/unit/pages/authRedirectOrigin.test.jsx — the auth redirect origin.
 *
 * THE DEFECT, REPRODUCED IN PRODUCTION
 * ------------------------------------
 * Clicking the email verification link landed on
 * `localhost:3000/#access_token=eyJhbGciOiJFUzI1NiIsImtpZCI6…` -> ERR_CONNECTION_REFUSED.
 * The token came back intact. The destination was wrong.
 *
 * Both `signInWithOtp` calls in `src/pages/AuthPage.jsx` passed
 * `{ shouldCreateUser: false }` and no `emailRedirectTo`, and `signUp` passed no `options`
 * at all. When `emailRedirectTo` is omitted, Supabase falls back to the project's dashboard
 * **Site URL**, which is `http://localhost:3000` — so every production auth email pointed at
 * a developer's dev server. `signInWithOAuth` in the same file was already correct
 * (`redirectTo: ${window.location.origin}/app/dashboard`), which is the in-repo precedent
 * the fix follows.
 *
 * WHAT THIS FILE ASSERTS, IN THE ORDER IT MATTERS
 * ----------------------------------------------
 *   1. **The origin is resolved from the running page, not from a literal.** The same
 *      resolver, given two different origins, returns two different redirects. This is the
 *      claim that makes the bundle correct in dev, on the CloudFront distribution, and after
 *      the custom-domain cutover, from ONE build.
 *   2. **Every Supabase call that can email the user a link carries an explicit redirect,
 *      and the list of those calls is re-derived from `src/` at test time.** A sixth call
 *      site added without a redirect reds this file; so does one added without being
 *      enumerated here. That is the drift-guard shape `tests/unit/lib/
 *      supabaseUnconfiguredClient.test.js` §4 already uses, and the reason it exists is the
 *      same: an enumeration maintained by hand silently stops being the whole list.
 *   3. **The redirect lands on a route the app actually serves**, verified against the
 *      router in `src/App.jsx` rather than assumed.
 *   4. **No production host literal is used to build it**, and **no `localhost` origin is
 *      baked in when the page is served from a non-localhost origin.**
 *
 * WHY THE ENVIRONMENT DOCBLOCK ABOVE SETS A NON-LOCALHOST URL
 * ----------------------------------------------------------
 * jsdom's default test URL is `http://localhost:3000` — by coincidence the exact wrong value
 * this defect was about. A file running at that origin could not tell a correct redirect from
 * the defect: both read `http://localhost:3000/...`. So this file runs jsdom at a real
 * non-localhost origin, and claim 4 becomes observable end-to-end: every redirect the real
 * components hand to Supabase must be on THAT origin, with no `localhost` anywhere in it.
 *
 * Only the network boundary is doubled. `src/config.js` is real, `pages/AuthPage.jsx` and
 * `pages/UpdatePasswordPage.jsx` are rendered for real and driven through their own forms,
 * and the recorded values are whatever those components actually computed.
 */

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';

import {
  AUTH_REDIRECT_PATH,
  getAuthRedirectUrl,
  getBrowserAppOrigin,
} from '../../../src/config';
import AuthPage from '../../../src/pages/AuthPage';
import UpdatePasswordPage from '../../../src/pages/UpdatePasswordPage';

const SRC_DIR = resolve(__dirname, '../../../src');
const APP_JSX = readFileSync(join(SRC_DIR, 'App.jsx'), 'utf8');

/** The origin the environment docblock puts jsdom on. */
const PAGE_ORIGIN = 'https://app.vyomquant.in';

// ---------------------------------------------------------------------------------------
// The recording client. Every auth method the app calls is present (the stand-in contract in
// src/supabase.js is what defines that surface); each one records its arguments verbatim and
// resolves the shape its real counterpart resolves, so the components take their success
// path and nothing here decides what the redirect should be.
// ---------------------------------------------------------------------------------------
const { supabaseCalls, supabaseStub } = vi.hoisted(() => {
  const supabaseCalls = [];
  const record = (method, result) => (...args) => {
    supabaseCalls.push({ method, args });
    return Promise.resolve(result);
  };
  const supabaseStub = {
    auth: {
      signInWithPassword: record('signInWithPassword', {
        data: { user: { id: 'u1' }, session: null },
        error: null,
      }),
      signInWithOtp: record('signInWithOtp', { data: { user: null, session: null }, error: null }),
      verifyOtp: record('verifyOtp', { data: { user: null, session: null }, error: null }),
      signUp: record('signUp', { data: { user: { id: 'u1' }, session: null }, error: null }),
      signInWithOAuth: record('signInWithOAuth', {
        data: { provider: 'google', url: null },
        error: null,
      }),
      updateUser: record('updateUser', { data: { user: { id: 'u1' } }, error: null }),
      signOut: record('signOut', { error: null }),
      getUser: record('getUser', { data: { user: null }, error: null }),
      onAuthStateChange: () => ({
        data: { subscription: { unsubscribe: () => {} } },
      }),
    },
  };
  return { supabaseCalls, supabaseStub };
});

vi.mock('../../../src/supabase', () => ({
  supabase: supabaseStub,
  default: supabaseStub,
  createUnconfiguredClient: () => supabaseStub,
}));

const callsTo = (method) => supabaseCalls.filter((entry) => entry.method === method);

/** Every redirect value handed to Supabase, from whichever option carried it. */
const recordedRedirects = () =>
  supabaseCalls
    .flatMap(({ args }) => args)
    .filter((arg) => arg && typeof arg === 'object')
    .flatMap((arg) => [arg.emailRedirectTo, arg.redirectTo, arg.options?.emailRedirectTo, arg.options?.redirectTo])
    .filter(Boolean);

beforeEach(() => {
  supabaseCalls.length = 0;
});

afterEach(() => {
  cleanup();
  if (vi.isFakeTimers()) {
    vi.clearAllTimers();
    vi.useRealTimers();
  }
});

// =======================================================================================
// 1. The origin is resolved from the running page
// =======================================================================================
describe('1. origin derivation', () => {
  it('reads the origin the page is actually served from', () => {
    expect(getBrowserAppOrigin()).toBe(PAGE_ORIGIN);
    expect(getAuthRedirectUrl()).toBe(`${PAGE_ORIGIN}${AUTH_REDIRECT_PATH}`);
  });

  it('returns a different redirect for a different origin', () => {
    // The whole point of the fix: ONE bundle, built once, served from more than one host.
    // The `location` parameter is a test seam — jsdom defines `window.location` and every
    // property on it as `[LegacyUnforgeable]`, so neither `Object.defineProperty` nor
    // `vi.stubGlobal` can swap the origin out (both throw "Cannot redefine property"), and
    // this claim cannot be made without injecting one.
    const cloudfront = getAuthRedirectUrl(AUTH_REDIRECT_PATH, { origin: 'https://dist.example.net' });
    const custom = getAuthRedirectUrl(AUTH_REDIRECT_PATH, { origin: 'https://app.example.com' });
    const dev = getAuthRedirectUrl(AUTH_REDIRECT_PATH, { origin: 'http://localhost:1420' });

    expect(cloudfront).toBe(`https://dist.example.net${AUTH_REDIRECT_PATH}`);
    expect(custom).toBe(`https://app.example.com${AUTH_REDIRECT_PATH}`);
    expect(dev).toBe(`http://localhost:1420${AUTH_REDIRECT_PATH}`);
    expect(new Set([cloudfront, custom, dev]).size).toBe(3);
  });

  it('normalises a trailing slash rather than emitting a double slash', () => {
    expect(getAuthRedirectUrl(AUTH_REDIRECT_PATH, { origin: 'https://app.example.com/' }))
      .toBe(`https://app.example.com${AUTH_REDIRECT_PATH}`);
  });

  it('accepts an explicit path for a non-dashboard landing route', () => {
    expect(getAuthRedirectUrl('/reset-password', { origin: 'https://app.example.com' }))
      .toBe('https://app.example.com/reset-password');
  });

  it('omitting the seam reads the page, it does not fall back', () => {
    // `undefined` is not "no origin": it is the default parameter, so every call site in
    // `src/` — none of which passes the seam — resolves the live page origin. Asserted
    // separately from the fallback below so the two cannot be conflated.
    expect(getBrowserAppOrigin(undefined)).toBe(PAGE_ORIGIN);
    expect(getBrowserAppOrigin()).toBe(PAGE_ORIGIN);
  });

  it('falls back only when there is genuinely no origin to read', () => {
    // An opaque origin (sandboxed iframe, `file://` document) reports the STRING "null"; a
    // context with no location at all reports nothing. Both are the unreachable-in-the-app
    // tail of the resolver, and both must still produce an ABSOLUTE url — a relative one
    // would be rejected by Supabase, which is a silent no-redirect, i.e. the defect back.
    for (const location of [{}, { origin: '' }, { origin: 'null' }]) {
      const fallback = getBrowserAppOrigin(location);
      expect(fallback).toMatch(/^https?:\/\/localhost:\d+$/);
      const url = getAuthRedirectUrl(AUTH_REDIRECT_PATH, location);
      expect(url).toBe(`${fallback}${AUTH_REDIRECT_PATH}`);
      expect(() => new URL(url)).not.toThrow();
    }
  });
});

// =======================================================================================
// 2. Every email-sending call carries an explicit redirect — enumerated from the source
// =======================================================================================

/**
 * The Supabase auth methods that can put a LINK in the user's inbox, and so must name where
 * that link goes. Two of them have no call site in this tree today and are listed anyway:
 * they are the two most likely next additions (a "forgot password" screen, a resend that
 * bypasses the OTP form), and a guard that only covers what exists is how this defect got
 * in.
 *
 * `signInWithPassword` and `verifyOtp` are deliberately absent: neither sends mail.
 */
const EMAIL_LINK_METHODS = Object.freeze([
  'signUp',                 // confirmation email
  'signInWithOtp',          // magic link beside the 6-digit code
  'signInWithOAuth',        // provider round-trip, `redirectTo`
  'updateUser',             // email-change confirmation, second argument
  'resetPasswordForEmail',  // no call site today
  'resend',                 // no call site today
]);

/** Enumerated by hand from `src/`, and re-derived from `src/` by the last test below. */
const EMAIL_LINK_CALL_SITES = Object.freeze([
  { file: 'App.jsx', method: 'updateUser', count: 1 },
  { file: 'pages/AuthPage.jsx', method: 'signInWithOAuth', count: 1 },
  { file: 'pages/AuthPage.jsx', method: 'signInWithOtp', count: 2 },
  { file: 'pages/AuthPage.jsx', method: 'signUp', count: 1 },
  { file: 'pages/UpdatePasswordPage.jsx', method: 'updateUser', count: 1 },
]);

const REDIRECT_OPTION = /\b(?:emailRedirectTo|redirectTo)\s*:/;
const REDIRECT_HELPER = /getAuthRedirectUrl\s*\(/;

function collectSourceFiles(dir) {
  const files = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) files.push(...collectSourceFiles(full));
    else if (/\.(js|jsx)$/.test(entry)) files.push(full);
  }
  return files;
}

/**
 * The text between the parentheses of the call opening at `openIndex`.
 *
 * Paren-counting, not a parser: the argument objects in these five call sites contain no
 * unbalanced parenthesis inside a string or a comment, and the tests below would red loudly
 * (a `null` extraction) rather than pass quietly if one ever did.
 */
function argumentTextAt(text, openIndex) {
  let depth = 0;
  for (let i = openIndex; i < text.length; i += 1) {
    if (text[i] === '(') depth += 1;
    else if (text[i] === ')') {
      depth -= 1;
      if (depth === 0) return text.slice(openIndex + 1, i);
    }
  }
  return null;
}

/** Every `supabase.auth.<EMAIL_LINK_METHOD>(...)` call under `src/`, with its arguments. */
function scanEmailLinkCallSites() {
  const found = [];
  for (const file of collectSourceFiles(SRC_DIR)) {
    const text = readFileSync(file, 'utf8');
    const pattern = /supabase\s*\.\s*auth\s*\.\s*(\w+)\s*\(/g;
    for (const match of text.matchAll(pattern)) {
      if (!EMAIL_LINK_METHODS.includes(match[1])) continue;
      const openIndex = match.index + match[0].length - 1;
      found.push({
        file: relative(SRC_DIR, file).split(sep).join('/'),
        method: match[1],
        args: argumentTextAt(text, openIndex),
      });
    }
  }
  return found;
}

describe('2. every email-sending call carries an explicit redirect', () => {
  const scanned = scanEmailLinkCallSites();

  it('finds the call sites at all, so a silent zero-match scan cannot pass', () => {
    expect(scanned.length).toBe(
      EMAIL_LINK_CALL_SITES.reduce((total, site) => total + site.count, 0),
    );
    for (const site of scanned) expect(site.args).not.toBeNull();
  });

  it.each(EMAIL_LINK_CALL_SITES)('$file $method() declares a redirect option', ({ file, method, count }) => {
    const sites = scanned.filter((site) => site.file === file && site.method === method);
    expect(sites).toHaveLength(count);
    for (const site of sites) {
      expect(site.args, `${file} ${method}() has no emailRedirectTo/redirectTo`).toMatch(REDIRECT_OPTION);
      // The option must be BUILT BY THE HELPER. A literal, a re-inlined
      // `${window.location.origin}` template or a second copy of the derivation would satisfy
      // the line above and is exactly what "put it in ONE place" rules out.
      expect(site.args, `${file} ${method}() does not use getAuthRedirectUrl()`).toMatch(REDIRECT_HELPER);
    }
  });

  it('the enumeration above is the whole list src/ actually contains', () => {
    const derived = scanned
      .reduce((acc, site) => {
        const key = `${site.file}::${site.method}`;
        acc.set(key, (acc.get(key) ?? 0) + 1);
        return acc;
      }, new Map());
    // Codepoint order, not `localeCompare`: the expectation below is a literal list and a
    // locale-dependent sort would make this test's outcome depend on the machine's locale.
    const sortKey = (site) => `${site.file}\u0000${site.method}`;
    const derivedList = [...derived.entries()]
      .map(([key, count]) => {
        const [file, method] = key.split('::');
        return { file, method, count };
      })
      .sort((a, b) => (sortKey(a) < sortKey(b) ? -1 : 1));
    expect(derivedList).toEqual([...EMAIL_LINK_CALL_SITES]);
  });

  it('no email-sending call reaches Supabase through a bare options object again', () => {
    // The pre-fix shape, named exactly: `shouldCreateUser` present, no redirect beside it.
    for (const site of scanned.filter((s) => s.method === 'signInWithOtp')) {
      expect(site.args).toMatch(/shouldCreateUser/);
      expect(site.args).toMatch(REDIRECT_OPTION);
    }
  });
});

// =======================================================================================
// 3. The redirect target is a route the app serves
// =======================================================================================
describe('3. the redirect target exists in the router', () => {
  it('is a declared route in src/App.jsx', () => {
    expect(APP_JSX).toContain(`path="${AUTH_REDIRECT_PATH}"`);
  });

  it('is reachable on arrival, because the guard reads the token out of the hash', () => {
    // Source-level, and said plainly: this asserts the mechanism is present in `App.jsx`,
    // not that a browser performed it. `/app/dashboard` sits behind `AuthGuard`, so a user
    // arriving with `#access_token=…` and nothing in `sessionStorage` would be bounced to
    // `/signin` unless the guard reads the hash SYNCHRONOUSLY during render —
    // `getEffectiveToken()` is what does that, and `AuthGuard` is what calls it.
    expect(APP_JSX).toMatch(/function getEffectiveToken\(\)/);
    expect(APP_JSX).toMatch(/hash\.includes\("access_token="\)/);
    expect(APP_JSX).toMatch(/function AuthGuard\(\)\s*\{\s*const token = getEffectiveToken\(\)/);
  });

  it('has its hash handler mounted above the routes, so it runs on this path too', () => {
    // `PasswordRecoveryHandler` stores the token and strips the hash on EVERY path; its own
    // `navigate('/app/dashboard')` branch only fires on `/signin`, `/signup` and `/`, which
    // is why the redirect has to name a route that needs no further hop. A `type=recovery`
    // hash is intercepted by the same handler and sent to `/reset-password` from wherever it
    // lands, so password recovery is unaffected by this target.
    const handlerAt = APP_JSX.indexOf('<PasswordRecoveryHandler />');
    const routesAt = APP_JSX.indexOf('<Routes>');
    expect(handlerAt).toBeGreaterThan(-1);
    expect(routesAt).toBeGreaterThan(-1);
    expect(handlerAt).toBeLessThan(routesAt);
  });
});

// =======================================================================================
// 4. The real components, driven through their own forms
// =======================================================================================

const AuthPageAt = ({ mode }) => (
  <MemoryRouter initialEntries={[mode === 'signup' ? '/signup' : '/signin']}>
    <AuthPage mode={mode} />
  </MemoryRouter>
);

const fillCredentials = ({ signup = false } = {}) => {
  fireEvent.change(document.getElementById('auth-email'), {
    target: { value: 'trader@example.com' },
  });
  fireEvent.change(document.getElementById('auth-password'), {
    target: { value: 'Str0ng!Passw0rd' },
  });
  if (signup) {
    fireEvent.change(document.getElementById('auth-confirm-password'), {
      target: { value: 'Str0ng!Passw0rd' },
    });
    fireEvent.click(document.querySelector('input[type="checkbox"]'));
  }
};

const submitCredentialForm = async () => {
  await act(async () => {
    fireEvent.submit(document.querySelector('form'));
  });
};

describe('4. the redirect the components actually send', () => {
  const expected = `${PAGE_ORIGIN}${AUTH_REDIRECT_PATH}`;

  it('sign-in dispatches the OTP with emailRedirectTo on the page origin', async () => {
    render(<AuthPageAt mode="signin" />);
    fillCredentials();
    await submitCredentialForm();

    const [otp] = callsTo('signInWithOtp');
    expect(otp).toBeDefined();
    expect(otp.args[0].options.emailRedirectTo).toBe(expected);
    // The second factor itself is untouched: the OTP is still requested for an existing
    // user only, and the password check still ran first.
    expect(otp.args[0].options.shouldCreateUser).toBe(false);
    expect(callsTo('signInWithPassword')).toHaveLength(1);
  });

  it('sign-up sends the confirmation email with emailRedirectTo on the page origin', async () => {
    render(<AuthPageAt mode="signup" />);
    fillCredentials({ signup: true });
    await submitCredentialForm();

    const [signUp] = callsTo('signUp');
    expect(signUp).toBeDefined();
    expect(signUp.args[0].options.emailRedirectTo).toBe(expected);
  });

  it('the resend dispatch carries it too, not just the first send', async () => {
    vi.useFakeTimers();
    render(<AuthPageAt mode="signin" />);
    fillCredentials();
    await submitCredentialForm();

    // The resend button replaces the cooldown counter only once the 60s cooldown expires.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(61_000);
    });
    await act(async () => {
      fireEvent.click(screen.getByText('Resend code'));
    });

    const otpCalls = callsTo('signInWithOtp');
    expect(otpCalls).toHaveLength(2);
    for (const call of otpCalls) {
      expect(call.args[0].options.emailRedirectTo).toBe(expected);
    }
  });

  it('the OAuth path reads the same helper instead of its own template literal', async () => {
    render(<AuthPageAt mode="signin" />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('google-auth-button'));
    });

    const [oauth] = callsTo('signInWithOAuth');
    expect(oauth).toBeDefined();
    expect(oauth.args[0].options.redirectTo).toBe(expected);
  });

  it('updateUser receives the redirect in its second argument', async () => {
    // `{ password }` alone emails no link, but `emailRedirectTo` lives in `updateUser`'s
    // SECOND argument and is what an email change would need. Asserting the argument
    // POSITION is the point: passing the option inside the attributes object would be
    // accepted silently by supabase-js and ignored.
    //
    // Fake timers because the success branch schedules a 2s `navigate`: left on real timers
    // it fires into an unmounted router while later tests are running.
    vi.useFakeTimers();
    render(
      <MemoryRouter initialEntries={['/reset-password']}>
        <UpdatePasswordPage />
      </MemoryRouter>,
    );
    fireEvent.change(document.querySelector('input[type="password"]'), {
      target: { value: 'Str0ng!Passw0rd' },
    });
    await act(async () => {
      fireEvent.submit(document.querySelector('form'));
    });

    expect(callsTo('updateUser')).toHaveLength(1);
    const [update] = callsTo('updateUser');
    expect(update.args[0]).toEqual({ password: 'Str0ng!Passw0rd' });
    expect(update.args[1].emailRedirectTo).toBe(expected);
  });

  it('never hands Supabase a localhost redirect while served from a non-localhost origin', async () => {
    // Three separate mounts on purpose. Submitting the credential form moves the page to the
    // OTP stage, which unmounts the Google button; clicking Google first latches
    // `loadingAction` and disables the form. Neither ordering reaches both paths in one mount,
    // and this test's claim is about ALL of the redirects, so it collects them across mounts.
    render(<AuthPageAt mode="signin" />);
    await act(async () => {
      fireEvent.click(screen.getByTestId('google-auth-button'));
    });
    cleanup();

    render(<AuthPageAt mode="signin" />);
    fillCredentials();
    await submitCredentialForm();
    cleanup();

    render(<AuthPageAt mode="signup" />);
    fillCredentials({ signup: true });
    await submitCredentialForm();

    const redirects = recordedRedirects();
    expect(redirects.length).toBeGreaterThanOrEqual(3);
    for (const redirect of redirects) {
      expect(redirect).toBe(expected);
      expect(redirect).not.toMatch(/localhost/);
      expect(redirect).not.toMatch(/127\.0\.0\.1/);
      expect(new URL(redirect).origin).toBe(PAGE_ORIGIN);
    }
  });
});

// =======================================================================================
// 5. No host literal builds the redirect
// =======================================================================================
describe('5. no host literal builds the redirect', () => {
  /**
   * The two production hosts the ONE bundle is served from. Neither may appear in the files
   * that construct the redirect: the bundle is built once for both, and a literal would be
   * wrong for whichever host it is not — the defect class `ALLOWED_API_HOSTS` exists for.
   */
  const PRODUCTION_HOSTS = [/vyomquant\.in/i, /cloudfront\.net/i];

  /** The files that participate in building the redirect. */
  const REDIRECT_FILES = ['config.js', 'App.jsx', 'pages/AuthPage.jsx', 'pages/UpdatePasswordPage.jsx'];

  it.each(REDIRECT_FILES)('%s contains no production host literal', (file) => {
    const text = readFileSync(join(SRC_DIR, ...file.split('/')), 'utf8');
    for (const host of PRODUCTION_HOSTS) {
      expect(text).not.toMatch(host);
    }
  });

  it('no line anywhere in src/ pairs a redirect option with a host literal', () => {
    // Scoped to redirect lines rather than to all of `src/`: a marketing link to the product
    // site is legitimate and this file has no business failing on one. A host literal on the
    // line that builds an auth redirect is not.
    const offenders = [];
    for (const file of collectSourceFiles(SRC_DIR)) {
      const lines = readFileSync(file, 'utf8').split(/\r?\n/);
      lines.forEach((line, index) => {
        if (!REDIRECT_OPTION.test(line)) return;
        if (!PRODUCTION_HOSTS.some((host) => host.test(line))) return;
        offenders.push(`${relative(SRC_DIR, file).split(sep).join('/')}:${index + 1}`);
      });
    }
    expect(offenders).toEqual([]);
  });

  it('the resolved redirect carries no hardcoded host for any origin', () => {
    for (const origin of ['https://app.example.com', 'https://dist.example.net', 'http://localhost:1420']) {
      const url = getAuthRedirectUrl(AUTH_REDIRECT_PATH, { origin });
      expect(new URL(url).origin).toBe(origin);
      for (const host of PRODUCTION_HOSTS) expect(url).not.toMatch(host);
    }
  });
});
