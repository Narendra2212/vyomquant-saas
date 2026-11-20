/**
 * tests/unit/lib/supabaseClientConfig.test.js
 *
 * FE-01 -- asserts `src/supabase.js` constructs the real Supabase client with
 * `flowType: 'pkce'`, the actual fix for "any access_token in the URL hash becomes the
 * session (login CSRF)" (see docs/launch_readiness/REMEDIATION_LEDGER.json, id "FE-01").
 *
 * `@supabase/supabase-js`'s `createClient` is mocked rather than called for real: this
 * environment has no live Supabase project, and the point of this file is only to prove
 * what OPTIONS this app hands the constructor, not to exercise the SDK itself (that is
 * `tests/unit/lib/supabaseUnconfiguredClient.test.js`'s job, for the no-env-vars
 * fallback path, and `tests/unit/authPkceRedirect.test.jsx`'s job for the app-level
 * consequences of this flag).
 *
 * `vi.stubEnv` is used rather than a module-level `.env` file so the "configured" branch
 * of `src/supabase.js` (which is normally unreachable in this suite -- there is no
 * `VITE_SUPABASE_URL` in the test environment, which is why every other test in this repo
 * exercises the `createUnconfiguredClient()` fallback instead) can be reached at all.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockCreateClient = vi.fn(() => ({
  auth: { onAuthStateChange: () => ({ data: { subscription: { unsubscribe: () => {} } } }) },
  from: () => ({}),
}));

vi.mock('@supabase/supabase-js', () => ({
  createClient: (...args) => mockCreateClient(...args),
}));

// A hostname/key shape that passes src/supabase.js's own `validateSupabaseConfig` (it
// requires an `*.supabase.co` https URL and a key at least 50 characters long) --
// otherwise this test would silently fall through to the unconfigured stand-in and never
// reach the `createClient` call it is asserting on.
const VALID_URL = 'https://abcdefghijklmnop.supabase.co';
const VALID_ANON_KEY = `anon-key-${'x'.repeat(50)}`;

describe('FE-01: the real Supabase client is constructed with flowType: "pkce"', () => {
  beforeEach(() => {
    mockCreateClient.mockClear();
    vi.stubEnv('VITE_SUPABASE_URL', VALID_URL);
    vi.stubEnv('VITE_SUPABASE_ANON_KEY', VALID_ANON_KEY);
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('calls createClient exactly once, with the validated URL and key', async () => {
    await import('../../../src/supabase.js');
    expect(mockCreateClient).toHaveBeenCalledTimes(1);
    const [url, key] = mockCreateClient.mock.calls[0];
    expect(url).toBe(VALID_URL);
    expect(key).toBe(VALID_ANON_KEY);
  });

  it('passes flowType: "pkce" in the auth options -- the FE-01 fix itself', async () => {
    await import('../../../src/supabase.js');
    const [, , options] = mockCreateClient.mock.calls[0];
    expect(options.auth.flowType).toBe('pkce');
  });

  it('leaves the pre-existing session options unchanged alongside the new flowType', async () => {
    await import('../../../src/supabase.js');
    const [, , options] = mockCreateClient.mock.calls[0];
    expect(options.auth).toMatchObject({
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
      flowType: 'pkce',
    });
  });
});
