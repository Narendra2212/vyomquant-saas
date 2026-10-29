/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Which supabase-js auth events carry a LIVE session
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * THE BUG THIS EXISTS TO CLOSE
 * ---------------------------
 * `sessionStorage.token` is, in this app's own words, "this app's own bookkeeping"
 * (src/supabase.js) mirroring the session supabase-js holds. Two places bridged the
 * library's session into that mirror, and BOTH listed the events by hand:
 *
 *   App.jsx     `session?.access_token && (sessionStorage.getItem("token") || event === "SIGNED_IN")`
 *   apiClient.js `event === 'TOKEN_REFRESHED' || event === 'SIGNED_IN'`
 *
 * Neither admitted `INITIAL_SESSION`, and supabase-js emits exactly that when it RESTORES
 * a persisted session on page load. The client is configured `persistSession: true` and
 * `autoRefreshToken: true`, so a returning user genuinely has a live session — and the app
 * refused to see it.
 *
 * WHAT THAT PRODUCED, which is worse than a missing mirror:
 *
 *   * `AuthRedirect` reads `supabase.auth.getSession()` and navigates a user WITH a
 *     session into the app.
 *   * `AuthGuard` reads `getEffectiveToken()`, which reads `sessionStorage`, finds
 *     nothing, and bounces the same user back out.
 *
 * So the two authorities disagreed, and the realtime layer could never authenticate:
 * `websocketClient.readSessionToken()` reads the same empty key, so `requestTicket` is
 * called with no credential and every `/ws/*` handshake is refused for want of a ticket.
 * Measured in production over five days: ZERO accepted websocket connections, and not one
 * successful `POST /api/auth/ws-ticket`.
 *
 * WHY A SHARED CONSTANT RATHER THAN ADDING ONE STRING TO EACH LIST
 * ---------------------------------------------------------------
 * The defect was two hand-maintained copies of the same list drifting from the library's
 * event vocabulary. Fixing it by editing both lists would leave two copies to drift again.
 * This is the one place the vocabulary is written down, and
 * `tests/unit/authSessionBridge.test.jsx` asserts both call sites consult it.
 *
 * `SIGNED_OUT` is deliberately NOT here. It is the event that CLEARS the mirror, and
 * conflating "a session exists" with "a session ended" is how a signed-out user keeps a
 * usable token.
 */

/**
 * supabase-js v2 auth events after which `session.access_token` is a live credential.
 *
 * `INITIAL_SESSION` — a persisted session restored on load. The one that was missing.
 * `SIGNED_IN`       — a fresh sign-in, or a completed PKCE code exchange.
 * `TOKEN_REFRESHED` — `autoRefreshToken` rotated the JWT; the mirror must follow or every
 *                     subsequent request presents a stale token.
 * `USER_UPDATED`    — a profile/email/password change reissues the session.
 *
 * @type {readonly string[]}
 */
export const SESSION_BEARING_AUTH_EVENTS = Object.freeze([
  'INITIAL_SESSION',
  'SIGNED_IN',
  'TOKEN_REFRESHED',
  'USER_UPDATED',
  // Reissues the access token with `aal2`. This app gates billing mutations on AAL2
  // (`require_aal2`), so a mirror still holding the pre-challenge token would have the
  // user pass MFA and then be refused anyway.
  'MFA_CHALLENGE_VERIFIED',
]);

/**
 * Does this event hand us a token worth mirroring?
 *
 * The `access_token` check is the caller's, not this helper's: an event in the list with no
 * session (supabase-js emits `INITIAL_SESSION` with `session === null` for a visitor who
 * has never signed in) must write nothing.
 *
 * @param {string} event
 * @returns {boolean}
 */
export const carriesLiveSession = (event) => SESSION_BEARING_AUTH_EVENTS.includes(event);

export default SESSION_BEARING_AUTH_EVENTS;
