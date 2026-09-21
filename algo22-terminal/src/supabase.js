/**
 * Supabase client configuration
 * Handles authentication and database operations
 */

import { createClient } from '@supabase/supabase-js';

// Environment variables
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

// Production-grade env validation
const validateSupabaseConfig = () => {
  const errors = [];
  
  if (!supabaseUrl) {
    errors.push('VITE_SUPABASE_URL is missing');
  } else {
    // Validate URL format
    try {
      const url = new URL(supabaseUrl);
      if (!url.hostname.endsWith('.supabase.co')) {
        errors.push(`VITE_SUPABASE_URL has invalid hostname: ${url.hostname}. Expected *.supabase.co`);
      }
      if (url.protocol !== 'https:') {
        errors.push(`VITE_SUPABASE_URL must use HTTPS protocol`);
      }
    } catch (e) {
      errors.push(`VITE_SUPABASE_URL is malformed: ${supabaseUrl}`);
    }
  }
  
  if (!supabaseAnonKey) {
    errors.push('VITE_SUPABASE_ANON_KEY is missing');
  } else if (supabaseAnonKey.length < 50) {
    errors.push('VITE_SUPABASE_ANON_KEY appears invalid (too short)');
  }
  
  if (errors.length > 0) {
    console.error('🔴 SUPABASE CONFIGURATION ERRORS:', errors);
    throw new Error(`Supabase configuration invalid: ${errors.join('; ')}`);
  }
  
  // Log successful validation
  console.log('🟢 Supabase configuration validated:', {
    url: supabaseUrl,
    hasAnonKey: !!supabaseAnonKey,
    anonKeyLength: supabaseAnonKey?.length
  });
};

const NOT_CONFIGURED_MESSAGE = 'Supabase not configured';

/**
 * The error every unconfigured-client call reports. `code` is a real field on both
 * `AuthError` and `PostgrestError`, so callers that already branch on `error.code`
 * (see `lib/waitlistApi.js`) get a machine-readable reason rather than a bare message.
 */
const notConfiguredError = () => {
  const error = new Error(NOT_CONFIGURED_MESSAGE);
  error.code = 'SUPABASE_NOT_CONFIGURED';
  return error;
};

/**
 * Postgrest query-builder methods this app actually chains, enumerated from the call
 * sites in `lib/waitlistApi.js` and `lib/downloadAnalyticsApi.js`. Each returns the
 * builder so a chain of any length terminates in an awaitable that resolves to the
 * not-configured result - `select('*').gte(...).eq(...)` used to die on the second
 * link, because `select` returned a plain result object with no `gte` on it.
 */
const QUERY_BUILDER_METHODS = [
  'select', 'insert', 'update', 'delete',
  'eq', 'gte', 'or', 'order', 'range', 'single',
];

const createUnconfiguredQuery = () => {
  const result = {
    data: null,
    error: notConfiguredError(),
    count: null,
    status: 0,
    statusText: NOT_CONFIGURED_MESSAGE,
  };
  const builder = {
    then: (onFulfilled, onRejected) => Promise.resolve(result).then(onFulfilled, onRejected),
    catch: (onRejected) => Promise.resolve(result).catch(onRejected),
    finally: (onFinally) => Promise.resolve(result).finally(onFinally),
  };
  QUERY_BUILDER_METHODS.forEach((method) => {
    builder[method] = () => builder;
  });
  return builder;
};

/**
 * Stand-in client used when Supabase env vars are absent, so the module can keep the
 * promise it logs: "App will run without Supabase functionality".
 *
 * Every method below exists because some module in `src/` calls it. Two of them are
 * load-bearing beyond their own feature:
 *
 *   - `auth.onAuthStateChange` is invoked at MODULE SCOPE by `src/apiClient.js`, so its
 *     absence made `import './apiClient.js'` throw a TypeError and took the whole app -
 *     and every test file that transitively imports apiClient - down at load time. It
 *     returns the real client's `{ data: { subscription } }` because `App.jsx`
 *     destructures it and calls `subscription.unsubscribe()` on cleanup. It does NOT
 *     invoke the callback: the real client's first emission is a session event, and
 *     synthesising a SIGNED_OUT here would clear the stored token on every boot.
 *   - `auth.mfa.*` is the whole of `pages/TwoFA.jsx` and the security read in
 *     `pages/Wizard.jsx`.
 *
 * Result shapes mirror supabase-js's own error path per method, including the detail
 * that `getUser` returns `data: { user: null }` rather than `data: null` - callers
 * destructure `data.user` with no optional chaining.
 *
 * When adding a call against `supabase` anywhere in `src/`, add it here too.
 * `tests/unit/lib/supabaseUnconfiguredClient.test.js` enumerates the contract.
 */
function createUnconfiguredClient() {
  return {
    auth: {
      signInWithPassword: () => ({ data: { user: null, session: null }, error: notConfiguredError() }),
      signInWithOtp: () => ({ data: { user: null, session: null }, error: notConfiguredError() }),
      verifyOtp: () => ({ data: { user: null, session: null }, error: notConfiguredError() }),
      signInWithOAuth: (credentials) => ({
        data: { provider: credentials?.provider ?? null, url: null },
        error: notConfiguredError(),
      }),
      signUp: () => ({ data: { user: null, session: null }, error: notConfiguredError() }),
      signOut: () => ({ error: null }),
      getUser: () => ({ data: { user: null }, error: notConfiguredError() }),
      updateUser: () => ({ data: { user: null }, error: notConfiguredError() }),
      onAuthStateChange: (callback) => ({
        data: {
          subscription: {
            id: 'supabase-not-configured',
            callback,
            unsubscribe: () => {},
          },
        },
      }),
      mfa: {
        listFactors: () => ({ data: null, error: notConfiguredError() }),
        challenge: () => ({ data: null, error: notConfiguredError() }),
        verify: () => ({ data: null, error: notConfiguredError() }),
        enroll: () => ({ data: null, error: notConfiguredError() }),
        unenroll: () => ({ data: null, error: notConfiguredError() }),
        getAuthenticatorAssuranceLevel: () => ({ data: null, error: notConfiguredError() }),
      },
    },
    from: () => createUnconfiguredQuery(),
  };
}

// Validate configuration before creating client
let supabase;
try {
  validateSupabaseConfig();
  // Create and export Supabase client
  supabase = createClient(supabaseUrl, supabaseAnonKey, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true
    }
  });
} catch (error) {
  console.error('Supabase configuration validation failed:', error.message);
  console.warn('App will run without Supabase functionality');
  supabase = createUnconfiguredClient();
}

export { supabase, createUnconfiguredClient };
export default supabase;
