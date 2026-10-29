/**
 * tests/unit/authSessionBridge.test.jsx
 *
 * THE DEFECT
 * ----------
 * `sessionStorage.token` is this app's mirror of the session supabase-js holds. Two places
 * bridged the library's events into it, and both listed the events by hand:
 *
 *   App.jsx      session?.access_token && (sessionStorage.getItem("token") || event === "SIGNED_IN")
 *   apiClient.js event === 'TOKEN_REFRESHED' || event === 'SIGNED_IN'
 *
 * Neither admitted `INITIAL_SESSION`, which is what supabase-js emits when it RESTORES a
 * persisted session on page load — and the client is configured `persistSession: true`,
 * `autoRefreshToken: true` (src/supabase.js). So a returning user had a live session the app
 * could not see, which produced two failures at once:
 *
 *   1. `AuthRedirect` reads `supabase.auth.getSession()` and navigates that user INTO the
 *      app; `AuthGuard` reads `getEffectiveToken()` -> `sessionStorage`, finds nothing, and
 *      bounces them back out. The two authorities disagreed.
 *   2. `websocketClient.readSessionToken()` reads the same empty key, so `requestTicket`
 *      had no credential and every `/ws/*` handshake was refused for want of a ticket.
 *      Measured in production over five days: ZERO accepted websocket connections and not
 *      one successful `POST /api/auth/ws-ticket`. The connection indicator could only read
 *      disconnected.
 *
 * WHAT IS ASSERTED
 * ----------------
 *   1. the vocabulary itself — `INITIAL_SESSION` is in, `SIGNED_OUT` is not;
 *   2. the apiClient bridge BEHAVIOURALLY: fire `INITIAL_SESSION` at the real listener and
 *      the token must land in `sessionStorage`;
 *   3. a source guard that neither call site hand-lists events any more, so the two copies
 *      cannot drift from the library's vocabulary again — which was the actual defect.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  SESSION_BEARING_AUTH_EVENTS,
  carriesLiveSession,
} from '../../src/auth/sessionEvents';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'src');
const read = (p) => readFileSync(join(SRC, p), 'utf8');

// ---------------------------------------------------------------------------
// 1. The vocabulary
// ---------------------------------------------------------------------------

describe('the session-bearing auth events', () => {
  it('includes INITIAL_SESSION, which is the event that was missing', () => {
    expect(SESSION_BEARING_AUTH_EVENTS).toContain('INITIAL_SESSION');
    expect(carriesLiveSession('INITIAL_SESSION')).toBe(true);
  });

  it.each(['SIGNED_IN', 'TOKEN_REFRESHED', 'USER_UPDATED', 'MFA_CHALLENGE_VERIFIED'])(
    '%s carries a live session',
    (event) => {
      expect(carriesLiveSession(event)).toBe(true);
    },
  );

  it('does NOT include SIGNED_OUT', () => {
    // SIGNED_OUT is the event that CLEARS the mirror. Treating it as session-bearing is how
    // a signed-out user keeps a usable token.
    expect(SESSION_BEARING_AUTH_EVENTS).not.toContain('SIGNED_OUT');
    expect(carriesLiveSession('SIGNED_OUT')).toBe(false);
  });

  it('rejects anything it does not know', () => {
    expect(carriesLiveSession('PASSWORD_RECOVERY')).toBe(false);
    expect(carriesLiveSession('')).toBe(false);
    expect(carriesLiveSession(undefined)).toBe(false);
  });

  it('is frozen, so a caller cannot widen it at runtime', () => {
    expect(Object.isFrozen(SESSION_BEARING_AUTH_EVENTS)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 2. The apiClient bridge, driven for real
// ---------------------------------------------------------------------------

describe('apiClient mirrors a restored session', () => {
  let handler;

  beforeEach(() => {
    vi.resetModules();
    sessionStorage.clear();
    handler = null;

    vi.doMock('../../src/supabase.js', () => ({
      supabase: {
        auth: {
          // Captured so the test can fire the events supabase-js would.
          onAuthStateChange: (cb) => {
            handler = cb;
            return { data: { subscription: { unsubscribe: () => {} } } };
          },
          signOut: vi.fn().mockResolvedValue({}),
        },
      },
    }));
  });

  afterEach(() => {
    vi.doUnmock('../../src/supabase.js');
    vi.resetModules();
    sessionStorage.clear();
  });

  const loadBridge = async () => {
    await import('../../src/apiClient.js');
    expect(handler, 'apiClient did not register an auth listener').toBeTypeOf('function');
    return handler;
  };

  it('writes the token on INITIAL_SESSION — a page load with a persisted session', async () => {
    const cb = await loadBridge();

    cb('INITIAL_SESSION', { access_token: 'restored.jwt.value' });

    expect(sessionStorage.getItem('token')).toBe('restored.jwt.value');
  });

  it('writes the token on SIGNED_IN and on TOKEN_REFRESHED', async () => {
    const cb = await loadBridge();

    cb('SIGNED_IN', { access_token: 'fresh.jwt' });
    expect(sessionStorage.getItem('token')).toBe('fresh.jwt');

    cb('TOKEN_REFRESHED', { access_token: 'rotated.jwt' });
    expect(sessionStorage.getItem('token')).toBe('rotated.jwt');
  });

  it('writes nothing when the event carries no session', async () => {
    const cb = await loadBridge();

    // supabase-js emits INITIAL_SESSION with session === null for a visitor who has never
    // signed in. That must not write, and must not throw.
    cb('INITIAL_SESSION', null);
    expect(sessionStorage.getItem('token')).toBeNull();
  });

  it('clears the mirror on SIGNED_OUT', async () => {
    const cb = await loadBridge();

    cb('SIGNED_IN', { access_token: 'fresh.jwt' });
    expect(sessionStorage.getItem('token')).toBe('fresh.jwt');

    cb('SIGNED_OUT', null);
    expect(sessionStorage.getItem('token')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 3. Neither call site may hand-list the events again
// ---------------------------------------------------------------------------

describe('both bridges read the shared vocabulary', () => {
  it.each([['App.jsx'], ['apiClient.js']])(
    '%s imports carriesLiveSession',
    (file) => {
      const source = read(file);
      expect(source).toMatch(/import\s*\{[^}]*carriesLiveSession[^}]*\}\s*from\s*['"][^'"]*auth\/sessionEvents/);
      expect(source).toContain('carriesLiveSession(event)');
    },
  );

  it.each([['App.jsx'], ['apiClient.js']])(
    '%s no longer decides session-bearing events with a literal',
    (file) => {
      // The exact shapes the defect had. Matched inside the auth listener's condition only —
      // a prose mention in a comment explaining the history is fine and expected, so the
      // patterns below are the executable forms, with no leading `//`.
      const executable = read(file)
        .split('\n')
        .filter((line) => !line.trim().startsWith('//') && !line.trim().startsWith('*'))
        .join('\n');

      expect(executable).not.toMatch(/event\s*===\s*["']SIGNED_IN["']\s*\)\s*\)\s*\{/);
      expect(executable).not.toMatch(/event\s*===\s*["']TOKEN_REFRESHED["']\s*\|\|\s*event\s*===\s*["']SIGNED_IN["']/);
    },
  );

  it('websocketClient still reads the key the bridges write', () => {
    // Non-vacuity for the whole fix: if the websocket client read some other key, mirroring
    // the session correctly would still leave the socket unauthenticated.
    const source = read('websocketClient.js');
    expect(source).toContain("sessionStorage.getItem('token')");
  });
});
