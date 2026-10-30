/**
 * Centralized configuration for Algo22 Terminal
 * Uses Vite's import.meta.env for environment variables with safe browser origin fallbacks
 */

// ─────────────────────────────────────────────────────────────────────────────────────────
// THE API AND SOCKET BASE: THE SERVED ORIGIN WINS OVER THE BUILD-TIME ENV
//
// THE DEFECT THIS REMOVES, MEASURED AGAINST PRODUCTION
// Both of these used to be env-FIRST. `VITE_API_URL` and `VITE_WS_URL` were set to the raw
// CloudFront distribution domain, and because Vite inlines env vars at build time the
// shipped bundle contained them as unconditional constants -- the origin fallback below was
// dead-code-eliminated entirely:
//
//     Af=()=>"https://d7d88qs4jmch.cloudfront.net"
//     Of=()=>"wss://d7d88qs4jmch.cloudfront.net"
//
// So every API call and the websocket ticket mint were addressed to that host, while the
// page was served from `app.vyomquant.in`. And `index.html`'s own CSP says:
//
//     connect-src 'self' https://*.vyomquant.in wss://*.vyomquant.in
//                 https://*.supabase.co wss://*.supabase.co ... ;
//
// The distribution domain is NOT in that list and there is no `https:` wildcard, so the
// BROWSER REFUSED EVERY ONE before it left. axios reports that as "Network Error" with no
// response, which the UI renders as "No connection to VyomQuant" on every panel, and the
// socket could never even fetch a ticket.
//
// It was invisible from the server side, which is why it survived: measured over five days
// of production logs, same-origin relative calls (`/api/config`, `/api/release.json`)
// arrived normally while NOT ONE cross-origin API call or websocket handshake appeared at
// all. Sign-in kept working the whole time, because `https://*.supabase.co` IS in the
// policy -- so the app looked authenticated and completely disconnected at once.
//
// WHY ORIGIN-FIRST IS THE FIX RATHER THAN WIDENING THE CSP
// One CloudFront distribution serves the bundle AND proxies `/api/*` and `/ws/*` to the
// ALB, so the origin the page came from is always able to answer them. Same-origin needs no
// CSP entry (`'self'` covers it), no CORS preflight, and cannot be broken by whichever host
// happened to be in a secret at build time -- which is the same "one bundle, two hosts"
// defect class the `appOrigin` comment below already describes. Widening the CSP to a raw
// `*.cloudfront.net` host would instead bless an origin that is also a common entry in
// browser tracker blocklists.
//
// THE ENV VAR IS STILL HONOURED, and is still required, for the two contexts that have no
// usable served origin:
//   * the Tauri desktop build, where `window.location` is `tauri://localhost`;
//   * `npm run dev`, served on `http://localhost:1420` with the API on another port and no
//     vite proxy (`vite.config.js` sets only `port`/`strictPort`).
// Both are non-https, which is exactly the condition that falls through to the env var. A
// production web deployment is always https, so it always takes its own origin.
// ─────────────────────────────────────────────────────────────────────────────────────────

/** Is this a page served over TLS, i.e. a real deployed web origin? */
const servedOverHttps = () =>
  typeof window !== 'undefined'
  && window.location?.protocol === 'https:'
  && Boolean(window.location.host);

const getBrowserApiBase = () => {
  if (servedOverHttps()) return window.location.origin;
  if (import.meta.env.VITE_API_URL) return import.meta.env.VITE_API_URL;
  return "http://localhost:8000";
};

const getBrowserWsBase = () => {
  if (servedOverHttps()) return `wss://${window.location.host}`;
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
