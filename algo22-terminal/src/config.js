/**
 * Centralized configuration for Algo22 Terminal
 * Uses Vite's import.meta.env for environment variables with safe browser origin fallbacks
 */

const getBrowserApiBase = () => {
  if (import.meta.env.VITE_API_URL) return import.meta.env.VITE_API_URL;
  if (typeof window !== 'undefined' && window.location.origin && window.location.origin !== 'null') {
    return window.location.origin;
  }
  return "http://localhost:8000";
};

const getBrowserWsBase = () => {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL;
  if (typeof window !== 'undefined' && window.location.host) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}`;
  }
  return "ws://localhost:8000";
};

// ─────────────────────────────────────────────────────────────────────────────────────────
// Auth email redirect origin
//
// THE DEFECT THIS EXISTS TO REMOVE
// Supabase falls back to the project's dashboard **Site URL** for any auth email whose
// call site omits `emailRedirectTo`. That Site URL is `http://localhost:3000`, so a
// production user clicking a verification link landed on
// `localhost:3000/#access_token=…` -> ERR_CONNECTION_REFUSED. The token came back intact;
// only the destination was wrong. Three call sites in `pages/AuthPage.jsx` and two
// `updateUser` calls omitted it. They now all read the origin from here.
//
// WHY ORIGIN-FIRST, WHERE `getBrowserApiBase` ABOVE IS ENV-FIRST
// `getBrowserApiBase` is env-first because the API can live on a DIFFERENT host than the
// page (a dedicated API hostname), so it genuinely needs configuring. An auth redirect has
// to land back on the app the user is already looking at, and ONE bundle is built once and
// served from BOTH the CloudFront distribution and the custom domain. An env-first
// resolution would bake whichever host was set at build time into that bundle and send the
// other host's users to the wrong origin -- the same defect class as the localhost Site
// URL, and the reason the deploy carries an `ALLOWED_API_HOSTS` allow-list at all. So
// `window.location.origin` wins here, and the env var is only a last-resort escape hatch
// for a context that has no `window`. No host literal belongs in this file.
// ─────────────────────────────────────────────────────────────────────────────────────────

/**
 * Reached only when there is no location to read AND no `VITE_APP_ORIGIN` -- node, SSR, a
 * unit test importing this module outside jsdom. Every caller runs from a browser click
 * handler, so in the app this is unreachable, and the test file asserts that a non-local
 * origin never resolves to it. Port 1420 is `vite.config.js`'s `server.port`
 * (`strictPort: true`), i.e. the origin `npm run dev` actually serves.
 */
const LOCAL_DEV_ORIGIN = "http://localhost:1420";

const stripTrailingSlash = (value) => String(value).replace(/\/+$/, "");

/**
 * The origin the running app is being served from.
 *
 * @param {{origin?: string}} [location] defaults to `window.location`. The parameter is a
 *   TEST SEAM and nothing in `src/` passes it: jsdom defines `window.location` and every
 *   property on it as `[LegacyUnforgeable]`, so neither `Object.defineProperty` nor
 *   `vi.stubGlobal` can swap the origin out, and a claim about behaviour across two
 *   origins cannot be made without injecting one of them.
 * @returns {string} an absolute origin with no trailing slash.
 */
export const getBrowserAppOrigin = (
  location = typeof window !== 'undefined' ? window.location : undefined,
) => {
  // `'null'` is the string an opaque origin (a sandboxed iframe, a `file://` document)
  // reports -- the same case `getBrowserApiBase` guards above.
  const origin = location?.origin;
  if (origin && origin !== 'null') return stripTrailingSlash(origin);
  if (import.meta.env?.VITE_APP_ORIGIN) return stripTrailingSlash(import.meta.env.VITE_APP_ORIGIN);
  return LOCAL_DEV_ORIGIN;
};

/**
 * Where an auth link drops the user once Supabase has exchanged it for a session.
 *
 * `/app/dashboard` is verified against the router in `src/App.jsx`, not assumed:
 *   - the route exists (`<Route path="/app/dashboard" …>`);
 *   - `AuthGuard` reads `access_token` out of `window.location.hash` synchronously via
 *     `getEffectiveToken()`, so the guard admits the arriving user on the FIRST render
 *     rather than bouncing them to `/signin`;
 *   - `PasswordRecoveryHandler` is mounted above `<Routes>`, so its hash handling runs on
 *     every path -- it stores the token and strips the hash here too. Its own
 *     `navigate('/app/dashboard')` branch is limited to `/signin`, `/signup` and `/`,
 *     which is exactly why the redirect must name a route that needs no further hop.
 *   - a `type=recovery` hash is intercepted by that same handler and sent to
 *     `/reset-password` from wherever it lands, so recovery is unaffected by this target.
 * It is also the target `signInWithOAuth` already used, so all auth entries now agree.
 */
export const AUTH_REDIRECT_PATH = "/app/dashboard";

/**
 * The absolute URL handed to Supabase as `emailRedirectTo` / `redirectTo`.
 *
 * ONE derivation for every call site: a fifth one cannot get it wrong, and the value
 * cannot drift between the OTP, sign-up, resend, OAuth and password paths.
 *
 * @param {string} [path=AUTH_REDIRECT_PATH] app-relative path to land on.
 * @param {{origin?: string}} [location] the same test seam as `getBrowserAppOrigin`.
 * @returns {string} absolute URL: the running origin followed by `path`.
 */
export const getAuthRedirectUrl = (path = AUTH_REDIRECT_PATH, location) => {
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${getBrowserAppOrigin(location)}${suffix}`;
};

export const CONFIG = {
  apiBaseUrl: getBrowserApiBase(),
  wsBaseUrl: getBrowserWsBase(),
  // 🔴 STEP 12: Request timeout - 30 seconds to prevent hanging requests
  REQUEST_TIMEOUT: 30000
};

export default CONFIG;
