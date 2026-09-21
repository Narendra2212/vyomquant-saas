/**
 * tests/unit/lib/supabaseUnconfiguredClient.test.js
 *
 * Contract test for the stand-in Supabase client in `src/supabase.js`.
 *
 * THE DEFECT
 * ----------
 * When `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` are absent, `src/supabase.js` logs
 * "App will run without Supabase functionality" and substitutes a stand-in client. That
 * promise was false. The stand-in carried eight `auth` methods and a four-method `from`,
 * and the app calls more than that:
 *
 *   - `auth.onAuthStateChange` is called at MODULE SCOPE by `src/apiClient.js:592`. With the
 *     method absent, `import './apiClient.js'` threw `TypeError: ...onAuthStateChange is not
 *     a function` at load time, so the app never booted and every test file that
 *     transitively imports apiClient died during collection - `usePanelState.test.js`,
 *     `errorCopy.property.test.js`, `socketCredential.test.js`,
 *     `billingSocketLifecycle.test.jsx`, `signalTraceExpansion.property.test.jsx` and the
 *     Portfolio / LiveTrading / SignalTrace / Billing page suites. This is what was red in
 *     CI, which has no Supabase secrets.
 *   - `auth.mfa.*` is the whole of `pages/TwoFA.jsx` and the security read in
 *     `pages/Wizard.jsx`.
 *   - the Postgrest builder is chained past its first link in `lib/waitlistApi.js` and
 *     `lib/downloadAnalyticsApi.js` - `select('*').gte(...).eq(...)` - and `select` returned
 *     a plain result object with no `gte` on it.
 *
 * WHAT THIS FILE ASSERTS
 * ----------------------
 * The method list below is enumerated from the actual call sites under `src/`, not guessed.
 * Section 4 re-derives it from the source at test time, so adding a new `supabase.auth.X()`
 * call anywhere in `src/` without adding `X` to the stand-in reds this file.
 *
 * The fix is the stand-in working correctly. No Supabase secret is added to CI and no
 * workflow is changed.
 */

import { describe, it, expect, vi } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';

import { createUnconfiguredClient } from '../../../src/supabase.js';

const SRC_DIR = resolve(__dirname, '../../../src');

// Enumerated from `src/`. Each entry names the module that calls it.
const AUTH_METHODS = [
  'signInWithPassword',          // pages/AuthPage.jsx
  'signInWithOtp',               // pages/AuthPage.jsx
  'verifyOtp',                   // pages/AuthPage.jsx
  'signInWithOAuth',             // pages/AuthPage.jsx
  'signUp',                      // pages/AuthPage.jsx
  'signOut',                     // apiClient.js, components/shell/AccountMenu.jsx
  'getUser',                     // App.jsx, AccountMenu.jsx, pages/TwoFA.jsx, pages/Wizard.jsx
  'updateUser',                  // App.jsx, pages/UpdatePasswordPage.jsx
  'onAuthStateChange',           // apiClient.js (MODULE SCOPE), App.jsx
];

const MFA_METHODS = [
  'listFactors',                    // pages/TwoFA.jsx
  'challenge',                      // pages/TwoFA.jsx
  'verify',                         // pages/TwoFA.jsx
  'enroll',                         // pages/TwoFA.jsx
  'unenroll',                       // pages/TwoFA.jsx
  'getAuthenticatorAssuranceLevel', // pages/Wizard.jsx
];

// Postgrest builder methods chained in lib/waitlistApi.js and lib/downloadAnalyticsApi.js.
const QUERY_METHODS = ['select', 'insert', 'update', 'delete', 'eq', 'gte', 'or', 'order', 'range', 'single'];

function collectSourceFiles(dir) {
  const files = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      files.push(...collectSourceFiles(full));
    } else if (/\.(js|jsx)$/.test(entry)) {
      files.push(full);
    }
  }
  return files;
}

describe('unconfigured Supabase stand-in client', () => {
  // ── 1. every method the app calls exists ────────────────────────────────────
  describe('1. surface', () => {
    it.each(AUTH_METHODS)('exposes auth.%s as a function', (method) => {
      const client = createUnconfiguredClient();
      expect(typeof client.auth[method]).toBe('function');
    });

    it.each(MFA_METHODS)('exposes auth.mfa.%s as a function', (method) => {
      const client = createUnconfiguredClient();
      expect(typeof client.auth.mfa[method]).toBe('function');
    });

    it('exposes from() returning a builder with every chained method', () => {
      const client = createUnconfiguredClient();
      const builder = client.from('waitlist');
      for (const method of QUERY_METHODS) {
        expect(typeof builder[method]).toBe('function');
      }
    });
  });

  // ── 2. the shapes callers destructure ──────────────────────────────────────
  describe('2. result shapes', () => {
    it('onAuthStateChange returns { data: { subscription: { unsubscribe } } }', () => {
      const client = createUnconfiguredClient();
      const callback = vi.fn();
      const { data } = client.auth.onAuthStateChange(callback);
      expect(typeof data.subscription.unsubscribe).toBe('function');
      // App.jsx's cleanup path: must not throw.
      expect(() => data.subscription.unsubscribe()).not.toThrow();
    });

    it('onAuthStateChange does not invoke the callback', () => {
      // apiClient.js clears the stored token on SIGNED_OUT. Synthesising an event here
      // would log the user out on every boot.
      const client = createUnconfiguredClient();
      const callback = vi.fn();
      client.auth.onAuthStateChange(callback);
      expect(callback).not.toHaveBeenCalled();
    });

    it('getUser returns data.user so callers can destructure it without optional chaining', () => {
      // pages/Wizard.jsx:26 and pages/TwoFA.jsx:34 both do `const { data: { user } } = ...`.
      const client = createUnconfiguredClient();
      const { data, error } = client.auth.getUser();
      expect(() => {
        const { user } = data;
        return user;
      }).not.toThrow();
      expect(data.user).toBeNull();
      expect(error).toBeInstanceOf(Error);
    });

    it.each(['signInWithPassword', 'signInWithOtp', 'verifyOtp', 'signUp'])(
      '%s returns { user: null, session: null } with an error',
      (method) => {
        const client = createUnconfiguredClient();
        const { data, error } = client.auth[method]({ email: 'a@b.co', password: 'x' });
        expect(data).toEqual({ user: null, session: null });
        expect(error.message).toBe('Supabase not configured');
      },
    );

    it('signInWithOAuth echoes the requested provider and reports no url', () => {
      const client = createUnconfiguredClient();
      const { data, error } = client.auth.signInWithOAuth({ provider: 'google' });
      expect(data).toEqual({ provider: 'google', url: null });
      expect(error).toBeInstanceOf(Error);
    });

    it('signOut succeeds, so a local session teardown is never blocked', () => {
      const client = createUnconfiguredClient();
      expect(client.auth.signOut()).toEqual({ error: null });
    });

    it.each(MFA_METHODS)('auth.mfa.%s reports the not-configured error', (method) => {
      const client = createUnconfiguredClient();
      const { data, error } = client.auth.mfa[method]({ factorId: 'f1' });
      expect(data).toBeNull();
      expect(error.message).toBe('Supabase not configured');
      expect(error.code).toBe('SUPABASE_NOT_CONFIGURED');
    });
  });

  // ── 3. the query builder survives a full chain ─────────────────────────────
  describe('3. query builder', () => {
    it('resolves a chain of any length rather than throwing on the second link', async () => {
      // The exact chain in lib/downloadAnalyticsApi.js:17-19.
      const client = createUnconfiguredClient();
      const query = client
        .from('download_analytics')
        .select('*')
        .gte('downloaded_at', new Date().toISOString())
        .eq('platform', 'windows');
      const { data, error } = await query;
      expect(data).toBeNull();
      expect(error.message).toBe('Supabase not configured');
    });

    it('resolves the insert().select().single() chain from lib/waitlistApi.js', async () => {
      const client = createUnconfiguredClient();
      const { data, error } = await client
        .from('waitlist')
        .insert([{ email: 'a@b.co' }])
        .select('id, email, created_at')
        .single();
      expect(data).toBeNull();
      expect(error.code).toBe('SUPABASE_NOT_CONFIGURED');
    });

    it('resolves the select().order().eq().or().range() chain from waitlistAdminApi', async () => {
      const client = createUnconfiguredClient();
      const { data, error, count } = await client
        .from('waitlist')
        .select('*', { count: 'exact' })
        .order('created_at', { ascending: false })
        .eq('status', 'pending')
        .or('name.ilike.%a%')
        .range(0, 49);
      expect(data).toBeNull();
      expect(count).toBeNull();
      expect(error).toBeInstanceOf(Error);
    });

    it('resolves update().eq().select().single() and delete().eq()', async () => {
      const client = createUnconfiguredClient();
      const updated = await client.from('waitlist').update({ status: 'x' }).eq('id', 1).select().single();
      expect(updated.error).toBeInstanceOf(Error);
      const deleted = await client.from('waitlist').delete().eq('id', 1);
      expect(deleted.error).toBeInstanceOf(Error);
    });
  });

  // ── 4. the surface cannot drift behind the call sites ──────────────────────
  describe('4. drift guard', () => {
    it('every supabase.auth.<method>() called under src/ exists on the stand-in', () => {
      const client = createUnconfiguredClient();
      const called = new Set();
      const mfaCalled = new Set();

      for (const file of collectSourceFiles(SRC_DIR)) {
        const text = readFileSync(file, 'utf8');
        for (const match of text.matchAll(/supabase\s*\.\s*auth\s*\.\s*mfa\s*\.\s*(\w+)\s*\(/g)) {
          mfaCalled.add(match[1]);
        }
        for (const match of text.matchAll(/supabase\s*\.\s*auth\s*\.\s*(\w+)\s*\(/g)) {
          if (match[1] !== 'mfa') called.add(match[1]);
        }
      }

      // Sanity: the scan must actually find the module-scope apiClient.js call.
      expect(called.has('onAuthStateChange')).toBe(true);
      expect(mfaCalled.size).toBeGreaterThan(0);

      const missing = [...called].filter((m) => typeof client.auth[m] !== 'function');
      const missingMfa = [...mfaCalled].filter((m) => typeof client.auth.mfa[m] !== 'function');
      expect({ missing, missingMfa }).toEqual({ missing: [], missingMfa: [] });
    });

    it('the enumerated lists in this file match what src/ actually calls', () => {
      const called = new Set();
      const mfaCalled = new Set();
      for (const file of collectSourceFiles(SRC_DIR)) {
        const text = readFileSync(file, 'utf8');
        for (const match of text.matchAll(/supabase\s*\.\s*auth\s*\.\s*mfa\s*\.\s*(\w+)\s*\(/g)) {
          mfaCalled.add(match[1]);
        }
        for (const match of text.matchAll(/supabase\s*\.\s*auth\s*\.\s*(\w+)\s*\(/g)) {
          if (match[1] !== 'mfa') called.add(match[1]);
        }
      }
      expect([...called].sort()).toEqual([...AUTH_METHODS].sort());
      expect([...mfaCalled].sort()).toEqual([...MFA_METHODS].sort());
    });
  });
});
