import React, { useState, useEffect, useCallback, lazy, Suspense } from "react";
import { Routes, Route, Navigate, Outlet, useNavigate, useLocation } from 'react-router-dom';
import * as Sentry from "@sentry/react";
import { AppStateProvider } from './AppState';
import wsClient from './websocketClient';
import { Lock } from "lucide-react";
import { supabase } from './supabase';
import { carriesLiveSession } from './auth/sessionEvents';
import { isDesktopApp } from './runtime/desktop';
import ErrorBoundary from './components/ErrorBoundary';
import Sidebar from './components/Sidebar';
import TopBar from './components/TopBar';
import SupportCenter from './components/SupportCenter';
import NotificationCenter from './components/NotificationCenter';
// `DesktopOnlyOverlay` is gone — the shell is gated by `shell/ResponsiveGate` instead
// (task 8.4/8.5), and task 27.3 deleted the component file once nothing imported it.
import ResponsiveGate, { SIDEBAR_WIDTH_PX, useViewportAccess } from './components/shell/ResponsiveGate';
// The sidebar's footer slot (task 8.8). It is the app's ONLY route to the seven pages
// §6.4 defers out of primary nav — profile, security log, billing, exchanges, risk,
// notifications, support — and to sign-out, so `Sidebar` is never rendered without it.
import AccountMenu from './components/shell/AccountMenu';
// The one route/nav table (task 8.1). Both the `Suspense` fallback's title and the
// `'navigate'` bridge's id resolution read from it, so neither can disagree with the
// sidebar about what a route is called or where an id points.
import { NAV_ENTRIES, routeTitle } from './components/shell/navigation';
// LEGAL-01. Imported directly rather than lazily: it decides whether analytics
// may load at all, and it is mounted outside any <Suspense>, so a lazy chunk
// would render nothing on the first paint — exactly the moment the question
// needs answering. It is a few hundred bytes.
import CookieConsent from './components/legal/CookieConsent';
// LEGAL-01. Signup records an acceptance, which covers new accounts and nothing
// else: 182 accounts already exist and NONE has a record, because the old
// checkbox was never persisted. Without this the table would be accurate about
// new users and permanently silent about every existing one.
import ReacceptanceGate from './components/legal/ReacceptanceGate';
// Direct module paths, not `components/ds/index.js`: the barrel pulls the module graph
// of every primitive, and the shell needs exactly these two (design.md §26).
import { PageHeader } from './components/ds/PageHeader';
import { LoadingState } from './components/ds/LoadingState';
// The ONE toast transport for BACKEND events (task 10.1, Requirements 16.1/16.2). Mounted
// in `ShellGrid` below, once. It holds a ref-counted module subscription, so a second
// mount anywhere cannot double a toast, and it decides nothing: every frame goes through
// `design/notificationPolicy`, which is default-closed.
import { useNotificationStream } from './hooks/useNotificationStream';
import {
  Inp, ToastContainer,
  LoadingProvider,
} from './components/common/primitives';
import { token } from './design/tokens';
import { Button } from './components/ui/Button';
// The one auth-redirect origin derivation (see `config.js`). Used by the local
// `UpdatePasswordPage` below, which is the component `/reset-password` actually renders.
import { getAuthRedirectUrl, AUTH_REDIRECT_PATH } from './config';

// Public routes (lazy)
const LandingPage    = lazy(() => import('./components/landing/LandingPage'));
const DownloadPage   = lazy(() => import('./components/download/DownloadPage'));
const AdminDashboard = lazy(() => import('./components/admin/AdminDashboard'));
const LegalPageRoute = lazy(() => import('./components/legal/LegalPage'));

// Auth / onboarding pages (lazy)
const AuthPage  = lazy(() => import('./pages/AuthPage'));
const TwoFA     = lazy(() => import('./pages/TwoFA'));
const Wizard    = lazy(() => import('./pages/Wizard'));
// LEGAL-01: ./pages/LegalPage was a second, divergent copy of the legal
// documents and is deleted. `/legal` now redirects to `/legal/terms'.

// Authenticated app pages (lazy - one chunk per route)
const Dashboard           = lazy(() => import('./pages/Dashboard'));
const LiveTrading         = lazy(() => import('./pages/LiveTrading'));
const Strategies          = lazy(() => import('./pages/Strategies'));
const StrategyDetail      = lazy(() => import('./pages/StrategyDetail'));
const SignalTrace         = lazy(() => import('./pages/SignalTrace'));
const StrategyBuilder     = lazy(() => import('./pages/StrategyBuilder'));
const Backtester          = lazy(() => import('./pages/Backtester'));
const StrategyMarketplace = lazy(() => import('./pages/StrategyMarketplace'));
const ExchangeManager     = lazy(() => import('./pages/ExchangeManager'));
const RiskSettings        = lazy(() => import('./pages/RiskSettings'));
const Billing             = lazy(() => import('./pages/Billing'));
const Profile             = lazy(() => import('./pages/Profile'));
const SecurityLogs        = lazy(() => import('./pages/SecurityLogs'));
const Portfolio           = lazy(() => import('./pages/Portfolio'));
const TradeHistory        = lazy(() => import('./pages/TradeHistory'));
const PaperTrading        = lazy(() => import('./pages/PaperTrading'));

/**
 * The route `Suspense` fallback for the PUBLIC and AUTH routes — `/`, `/download`, the
 * five `/legal/*`, the public `/marketplace`, `/admin/waitlist`, `/signin`, `/signup`,
 * `/2fa` and `/wizard`.
 *
 * DELIBERATELY NOT `ShellRouteFallback`. Those routes render outside the shell, and
 * `routeTitle` answers for `/app/*` only — so a page header here would either carry no
 * title at all or, worse, a sidebar route title over the sign-in screen, labelling a
 * page that has no shell to label. This reserves the viewport and claims nothing else.
 *
 * `bg-surface-canvas` rather than the `background: '#080A0E'` literal this carried
 * before: the token resolves to that exact colour in `styles/tokens.css`, which is the
 * one place the value is written down (Requirement 1.1). `min-h-screen` replaces the
 * inline `minHeight: '100vh'` for the same reason of staying in the utility layer.
 *
 * `role="status"` with an `sr-only` name, because a blank screen announces nothing: a
 * screen-reader user waiting on a chunk otherwise gets silence.
 */
const PUBLIC_FALLBACK = (
  <div className="min-h-screen w-full bg-surface-canvas" role="status" aria-busy="true">
    <span className="sr-only">Loading</span>
  </div>
);

/**
 * The route `Suspense` fallback for the authenticated `/app/*` routes (design.md §6.2 (4)).
 *
 * A COMPONENT and not a module-level element, which is the whole reason the two
 * fallbacks are not one: the title depends on the current pathname, and `useLocation()`
 * can only be called from a component rendered inside the router.
 *
 * REQUIREMENT 2.2 IS WHY THIS EXISTS. The bare `<div style={{background:'#080A0E'}} />`
 * it replaces reserved no structure, so every lazy chunk boundary was a blank content
 * column that then populated — the page header appearing and pushing the content down
 * one frame after the route had already changed. `PageHeader` is a fixed
 * `PAGE_HEADER_HEIGHT_PX` (64px) for every combination of its props, so rendering it
 * here with the SAME title the page will render means the heading is identical before
 * and after the chunk resolves and nothing moves.
 *
 * `routeTitle` returns `null` for a path the table does not know. `/app/*`'s catch-all
 * redirects those to the dashboard, so the header is omitted rather than given an
 * invented heading (Requirement 19.4) — and `PageHeader` requires a real `title`,
 * throwing in development on a blank one, so this is also the honest call.
 *
 * NOT `kind="page"`, which is what §6.2 (4) prescribed alongside the real header before
 * this correction. `page` renders `LoadingState`'s `PageSkeleton`, and that shape OPENS
 * with its own `SKELETON_GEOMETRY.page.headerHeight` (64px) block — two bars standing in
 * for a title and a subtitle. It is the ALTERNATIVE to a real header, not a companion to
 * one. Rendering both stacked 64 + 64 = 128px of header above a page that resolves to
 * 64px, so every chunk boundary shifted the content down 64px on arrival: the exact
 * Requirement 2.2 defect this fallback exists to remove, inverted.
 *
 * `skeleton-table` is that shape minus the duplicate block — `PageSkeleton` renders
 * `TableSkeleton` as its own body, on the same 5×4 default counts — so the content this
 * reserves is unchanged and only the second heading is gone. `gap-4` is 16px, which is
 * `SKELETON_GEOMETRY.page.gap`: the gap `PageSkeleton` itself puts between its header
 * and its table.
 */
function ShellRouteFallback() {
  const { pathname } = useLocation();
  const title = routeTitle(pathname);

  return (
    <div className="flex w-full min-w-0 flex-col gap-4 p-6">
      {title === null ? null : <PageHeader title={title} />}
      {/* `label` rather than the default `'Loading'`: a route change announces which
          page is arriving, not that something unnamed is. */}
      <LoadingState
        kind="skeleton-table"
        label={title === null ? 'Loading page' : `Loading ${title}`}
      />
    </div>
  );
}

/** One element, so the ~20 shell routes below share it rather than each allocating one. */
const SHELL_FALLBACK = <ShellRouteFallback />;

/**
 * The route an `id` dispatched on the `'navigate'` DOM event resolves to, or `null`.
 *
 * Replaces `AppShell`'s 15-entry `PATH_MAP` plus its `|| \`/app/${e.detail}\`` fallback.
 * That fallback was the actual problem: it turned ANY string any caller dispatched into
 * a route push, so a typo or a stale id pushed `/app/<nonsense>` and the `/app/*`
 * catch-all silently bounced the user to the dashboard. `null` here means "not a route",
 * and the listener does nothing with it (Requirement 19.4).
 *
 * Three sources, in order, all of them `shell/navigation.js`'s tables rather than a
 * second copy of the route list:
 *
 *   1. `NAV_ENTRIES` — the ten sidebar entries, by id, giving the entry's own canonical
 *      `path`. This is why `backtest` resolves to `/app/backtest` and not to a string
 *      rebuilt from the id.
 *   2. `routeTitle('/app/<id>')` — the seven routes §6.4 moved into the account menu
 *      (`exchange`, `risk`, `billing`, `profile`, `support`, `notifications`,
 *      `security-logs`) plus `/app/2fa`. `PATH_MAP` carried all seven and their routes
 *      are untouched, so they must stay reachable; a lookup against `NAV_ENTRIES` alone
 *      would have broken every one of them. `SECONDARY_ROUTE_TITLES` is keyed by exactly
 *      those paths, so a non-`null` title IS the recognition test — no separate list.
 *   3. `landing`, the one id that does not name an `/app/*` route. `navigation.js` is the
 *      AUTHENTICATED table and has no public routes in it, so this cannot come from
 *      there; it is one named branch rather than a table, so it cannot grow back into
 *      `PATH_MAP`.
 *
 * @param {unknown} id A `CustomEvent.detail`, so genuinely unknown.
 * @returns {string | null}
 */
function navigationTargetForId(id) {
  if (typeof id !== 'string' || id === '') return null;

  const entry = NAV_ENTRIES.find((navEntry) => navEntry.id === id);
  if (entry) return entry.path;

  const appPath = `/app/${id}`;
  if (routeTitle(appPath) !== null) return appPath;

  if (id === 'landing') return '/';

  return null;
}

// PASSWORD RESET PAGE
function UpdatePasswordPage() {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  const handleUpdate = async (e) => {
    e.preventDefault();
    if (!password) { setError("Password cannot be empty."); return; }
    setLoading(true); setError(""); setSuccess(false);
    try {
      const { supabase } = await import('./supabase');
      // `{ password }` alone sends no link, but `updateUser`'s SECOND argument is where
      // `emailRedirectTo` lives, and it is the option an email change (`{ email }`) would
      // need. Declaring it here means the confirmation link cannot silently fall back to
      // the project's Site URL -- `http://localhost:3000` -- the way the OTP link did.
      const { error: supaError } = await supabase.auth.updateUser(
        { password },
        { emailRedirectTo: getAuthRedirectUrl() },
      );
      if (supaError) throw supaError;
      setSuccess(true);
      setTimeout(() => navigate("/app/dashboard"), 2000);
    } catch (err) {
      setError(err.message || "Failed to update password.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ background: token.surface.canvas, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ background: token.surface.raised, border: `1px solid ${token.line.default}`, borderRadius: 18, padding: 36, width: 450 }}>
        <h1 style={{ color: token.content.primary, fontWeight: 900, fontSize: 22, marginBottom: 20, textAlign: "center" }}>
          Reset Password
        </h1>
        {success ? (
          <div style={{ color: token.status.profit.fg, fontSize: 13, textAlign: "center", background: `${token.status.profit.fg}12`, padding: 12, borderRadius: 8 }}>
            Password updated! Redirecting to dashboard...
          </div>
        ) : (
          <form onSubmit={handleUpdate} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <Inp
              lbl="New Password"
              ph="••••••••••••"
              type="password"
              icon={Lock}
              val={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={loading}
            />
            <Button variant="primary" size="md" className="w-full justify-center mt-2" disabled={loading}>
              {loading ? "Updating..." : "Update Password"}
            </Button>
            {!!error && (
              <div style={{ marginTop: 10, color: token.status.loss.fg, fontSize: 12, textAlign: "center" }}>{error}</div>
            )}
          </form>
        )}
      </div>
    </div>
  );
}

// Whether a token is DEMONSTRABLY past its own `exp`.
//
// Only a token this can PROVE is dead returns true. A non-JWT, a malformed one, or a JWT
// carrying no `exp` returns false and is left exactly as it was: the job here is to stop
// presenting a credential that is certainly spent, not to invent a policy for shapes it
// cannot read. The SIGNATURE is deliberately not checked - the server holds the secret and
// rejects a forgery on every request. What only the client can notice is that the token it
// is about to present has already expired.
function isDemonstrablyExpiredJwt(token) {
  if (typeof token !== "string") return false;
  const segments = token.split(".");
  if (segments.length !== 3) return false;
  try {
    const b64 = segments[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4)));
    if (typeof payload?.exp !== "number") return false;
    return payload.exp * 1000 <= Date.now();
  } catch {
    return false;
  }
}

// FE-01: the paths a legitimate Supabase auth redirect can actually land on.
// `getAuthRedirectUrl` (config.js) hands Supabase this exact path for every OTP/sign-up/
// resend/OAuth call site, so neither a PKCE `?code=` nor a legacy `#access_token=` has any
// legitimate reason to arrive anywhere else. `/signin`/`/signup`/`/` stay in the allowlist
// because `PasswordRecoveryHandler` below forwards a `code` landing on any of those three
// toward the dashboard too, once it resolves (the same thing it used to do for a stray
// implicit hash).
const _AUTH_REDIRECT_LANDING_PATHS = [AUTH_REDIRECT_PATH, "/signin", "/signup", "/"];

// Exported for tests (FE-01) -- not used by any other module. Renamed from
// `_isLegitimateAuthHashLandingPath`: it now gates a query-string `code=`, not a hash.
export function _isLegitimateAuthRedirectLandingPath(pathname) {
  return _AUTH_REDIRECT_LANDING_PATHS.includes(pathname);
}

// FE-01: true when the current URL looks like an in-flight PKCE callback this browser
// might be mid-exchange for -- a `code` query parameter, landing on one of the paths
// Supabase is actually ever told to redirect to. `exchangeCodeForSession` (triggered
// automatically by `detectSessionInUrl` in src/supabase.js) is a network round-trip, so
// `sessionStorage` has not been populated yet on the very first render of a route that
// just arrived this way. `AuthGuard`/`GuestGuard` use this to show a brief loading state
// and wait for that exchange to settle instead of bouncing the arriving user to `/signin`
// (or rendering the sign-in form over them) before Supabase has even answered.
export function hasPendingAuthCodeExchange() {
  return (
    typeof window !== "undefined" &&
    typeof window.location.search === "string" &&
    /(?:^|[?&])code=/.test(window.location.search) &&
    _isLegitimateAuthRedirectLandingPath(window.location.pathname)
  );
}

// Synchronously extracts auth token from sessionStorage.
//
// FE-01 history: this used to ALSO read `window.location.hash` for `access_token=` -- at
// first unconditionally on every guarded route, then (a first FE-01 pass, commit 83c8a8c)
// only on an allowlisted path -- and adopt whatever it found as the session if the token
// was not demonstrably expired. Both were defects, and the second only narrowed where the
// defect was reachable through THIS function specifically: a non-expired token in a hash
// proves only that Supabase signed it for SOMEONE, not that it was issued to this browser,
// and `@supabase/supabase-js`'s OWN `detectSessionInUrl` was separately adopting the exact
// same hash on ANY route the moment the page loaded -- before any React code ran at all --
// and bridging it into this app's session via the `onAuthStateChange` listener in
// `PasswordRecoveryHandler` below. That SDK-level path was never narrowed by the first
// FE-01 pass, and it is the one a login-CSRF link actually used.
//
// The real fix is `flowType: 'pkce'` in src/supabase.js. A legitimate OAuth / magic-link /
// sign-up redirect now lands as `?code=...`, never `#access_token=...`, and supabase-js
// exchanges that code for a session only if THIS browser also holds the matching
// `code_verifier` it generated when the flow was started -- an attacker's own valid `code`
// has no such verifier here, so the exchange fails and nothing is adopted. supabase-js now
// also actively REJECTS a stray `#access_token=` hash once `flowType: 'pkce'` is
// configured (a flow-type mismatch), rather than adopting it the way it used to. So this
// function has nothing left to safely read out of a hash: a real redirect never produces
// one for these flows any more, and one that still arrives is either the attack this
// finding is about or a stale pre-PKCE email link -- and the SDK itself now refuses both.
// A resolved PKCE exchange reaches `sessionStorage` via the same `onAuthStateChange`
// bridge; see `hasPendingAuthCodeExchange` and `useGuardedAuthToken` above/below for how
// the guards wait for that bridge instead of reading a hash by hand.
export function getEffectiveToken() {
  let token = sessionStorage.getItem("token");
  // An expired token is not a session. Both guards read this one function, so discarding it
  // here is what stops `AuthGuard` admitting a dead credential to the shell - every read
  // behind it would 401 - and what stops `GuestGuard` bouncing a signed-out user off
  // `/signin` forever because a spent token was still sitting in storage.
  if (isDemonstrablyExpiredJwt(token)) {
    sessionStorage.removeItem("token");
    token = null;
  }
  return token;
}

/**
 * FE-01: the one place `AuthGuard` and `GuestGuard` both decide "is there a session",
 * now that the answer can require waiting on an in-flight PKCE code exchange rather than
 * being available synchronously the way a hash-borne implicit token was.
 *
 * Returns `{ pending: true, token: null }` while supabase-js's own init/exchange promise
 * is still outstanding for a `code` on an allowlisted landing path (see
 * `hasPendingAuthCodeExchange`), and `{ pending: false, token }` once that has settled --
 * or immediately, with no render delay, when there was never anything to wait for (the
 * overwhelmingly common case: a normal in-app navigation with an already-established
 * `sessionStorage` token or none at all).
 */
function useGuardedAuthToken() {
  const [state, setState] = useState(() => {
    const token = getEffectiveToken();
    if (token) return { pending: false, token };
    return hasPendingAuthCodeExchange()
      ? { pending: true, token: null }
      : { pending: false, token: null };
  });

  useEffect(() => {
    if (!state.pending) return;
    let cancelled = false;
    // `getSession()` awaits supabase-js's own `initializePromise`, which is the SAME
    // promise running the automatic `exchangeCodeForSession` this is waiting on -- so this
    // resolves only once the SDK has either adopted a genuine code (verifier matched) or
    // given up (no match / error), never before. Its own result is not read directly:
    // the `onAuthStateChange` listener in `PasswordRecoveryHandler` is what writes
    // `sessionStorage`, exactly as it does for every other sign-in path, so re-reading
    // `getEffectiveToken()` here is what stays in sync with that single source of truth
    // rather than trusting this call's session shape a second way.
    supabase.auth.getSession().finally(() => {
      if (!cancelled) setState({ pending: false, token: getEffectiveToken() });
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.pending]);

  return state;
}

// The brief "waiting on the PKCE exchange" screen. Styled like `AdminGuard`'s own loading
// branch below, so the two momentary auth-wait screens this app can show do not diverge.
function AuthExchangePendingScreen() {
  return (
    <div style={{ background: token.surface.canvas, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <span className="font-mono" style={{ color: token.content.muted, fontSize: 12 }}>Completing sign-in…</span>
    </div>
  );
}

// AUTH GUARDS. Exported (alongside `AuthExchangePendingScreen`) for
// tests/unit/authPkceRedirect.test.jsx (FE-01) -- not used by any other module.
export function AuthGuard() {
  const { pending, token: effectiveToken } = useGuardedAuthToken();
  if (pending) return <AuthExchangePendingScreen />;
  if (!effectiveToken) return <Navigate to="/signin" replace />;
  return <Outlet />;
}

export function GuestGuard() {
  const { pending, token: effectiveToken } = useGuardedAuthToken();
  if (pending) return <AuthExchangePendingScreen />;
  if (effectiveToken) return <Navigate to="/app/dashboard" replace />;
  return <Outlet />;
}

// Admin-only guard: verifies Supabase session and app_metadata.role === 'admin'.
// No waitlist data is fetched until this resolves to an authorized admin.
function AdminGuard() {
  const [status, setStatus] = useState('loading'); // 'loading' | 'authorized' | 'denied'

  useEffect(() => {
    let cancelled = false;
    async function checkAdmin() {
      try {
        const { data: { user }, error } = await supabase.auth.getUser();
        if (cancelled) return;
        if (error || !user) { setStatus('denied'); return; }
        const role = user.app_metadata?.role;
        setStatus(role === 'admin' ? 'authorized' : 'denied');
      } catch {
        if (!cancelled) setStatus('denied');
      }
    }
    checkAdmin();
    return () => { cancelled = true; };
  }, []);

  if (status === 'loading') {
    return (
      <div style={{ background: token.surface.canvas, minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {/* `font-mono` rather than the bare `monospace` keyword this carried before:
            the keyword resolves to whatever the browser defaults to, while the class
            resolves `--font-mono` and so actually reaches the JetBrains Mono that
            index.html loads. */}
        <span className="font-mono" style={{ color: token.content.muted, fontSize: 12 }}>Verifying access…</span>
      </div>
    );
  }
  if (status === 'denied') {
    return (
      <div style={{ background: token.surface.canvas, minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
        <Lock size={32} style={{ color: token.status.loss.fg }} />
        <div style={{ color: token.content.primary, fontWeight: 700, fontSize: 16 }}>Access Denied</div>
        {/* Same correction as the loading branch above: the real mono stack, not the
            bare `monospace` keyword. */}
        <div className="font-mono" style={{ color: token.content.muted, fontSize: 11 }}>Administrator credentials required.</div>
      </div>
    );
  }
  return <Outlet />;
}

// Intercepts Supabase password-recovery hashes and in-flight PKCE code callbacks on mount
function PasswordRecoveryHandler() {
  const navigate = useNavigate();
  useEffect(() => {
    const hash = window.location.hash;
    if (hash && hash.includes("type=recovery")) {
      // NOT path-scoped, and NOT routed through supabase-js's own PKCE exchange: nothing
      // in src/ calls `resetPasswordForEmail` with an explicit `redirectTo`, so this app
      // does not control -- and cannot assert -- which path a legitimate recovery email
      // actually lands on, or what flow type generated it. Still rejects a demonstrably
      // expired token; the login-CSRF exposure this branch carries is recorded as a known,
      // UNCHANGED FE-01 residual (see docs/launch_readiness/REMEDIATION_LEDGER.json),
      // not narrowed on a guess and not touched by the `flowType: 'pkce'` switch below --
      // switching this app's own client's flow type cannot make a link some OTHER,
      // uncontrolled sender generates any safer. One real behaviour change IS worth
      // flagging here even though this branch itself is unchanged: with `flowType: 'pkce'`
      // now configured (src/supabase.js), supabase-js's OWN internal session detection
      // will REJECT this same implicit-shaped hash as a flow-type mismatch rather than
      // quietly adopting it into its real internal session the way it used to -- so if the
      // out-of-band recovery email is still implicit-shaped, the `updateUser` auth call
      // on `/reset-password` (which needs THAT internal session, not just the
      // `sessionStorage` bookkeeping this branch does by hand) could now fail where it
      // previously, insecurely, succeeded. Needs a live project to confirm either way.
      const params = new URLSearchParams(hash.startsWith("#") ? hash.substring(1) : hash);
      const token = params.get("access_token");
      if (token && !isDemonstrablyExpiredJwt(token)) {
        sessionStorage.setItem("token", token);
        window.history.replaceState(null, "", window.location.pathname);
        navigate("/reset-password", { replace: true });
      }
    }
    // The former `else if (hash.includes("access_token=") ...)` branch -- the non-recovery
    // implicit-flow adoption an earlier FE-01 pass narrowed to an allowlisted path -- is
    // DELETED here, not narrowed further. Under `flowType: 'pkce'` (src/supabase.js) a
    // legitimate OAuth / magic-link / sign-up redirect never produces that shape again,
    // and supabase-js's own `_initialize()` now actively REJECTS a `#access_token=` hash
    // as a flow-type mismatch rather than adopting it -- so there is nothing left here to
    // safely read out of a hash for that case, and hand-rolling a second, duplicate
    // acceptance of it would just reopen what this fix exists to close. A legitimate
    // redirect now lands as `?code=...`, which supabase-js's own `detectSessionInUrl`
    // exchanges automatically against the locally-held verifier (see `getEffectiveToken`
    // and `useGuardedAuthToken` above) -- deliberately not re-implemented here by hand.

    // FE-01: forward a resolving PKCE sign-in that landed on `/`, `/signin` or `/signup`
    // on to the dashboard -- the same three non-dashboard paths the deleted implicit-flow
    // branch above used to forward from. `AuthGuard`/`GuestGuard` already handle
    // `AUTH_REDIRECT_PATH` itself (it needs no further hop), so this only ever fires for
    // the other three. The exchange itself is already running (supabase-js kicked it off
    // when this module was first imported); this just waits for it and acts once it
    // settles, rather than re-triggering a second one.
    if (
      hasPendingAuthCodeExchange() &&
      window.location.pathname !== AUTH_REDIRECT_PATH
    ) {
      supabase.auth.getSession().then(({ data }) => {
        if (data?.session?.access_token) {
          navigate(AUTH_REDIRECT_PATH, { replace: true });
        }
      });
    }

    // Subscribe to onAuthStateChange for live session events
    if (supabase?.auth?.onAuthStateChange) {
      const { data: authListener } = supabase.auth.onAuthStateChange((event, session) => {
        /*
          `carriesLiveSession(event)` rather than a literal. This condition used to read
          `sessionStorage.getItem("token") || event === "SIGNED_IN"`, which admitted a
          restored session only if THIS TAB had already mirrored one. supabase-js emits
          `INITIAL_SESSION` when it restores a persisted session — and the client is
          configured `persistSession: true` — so a returning user satisfied neither arm and
          had a live session this app could not see.

          That left the two authorities disagreeing: `AuthRedirect` above reads
          `supabase.auth.getSession()` and navigates such a user INTO the app, while
          `AuthGuard` reads `getEffectiveToken()`, finds `sessionStorage` empty, and bounces
          them straight back out. It also left the realtime layer unable to authenticate at
          all, because `websocketClient.readSessionToken()` reads this same key.
          See `auth/sessionEvents.js`.
        */
        if (session?.access_token && carriesLiveSession(event)) {
          sessionStorage.setItem("token", session.access_token);
        } else if (event === "SIGNED_OUT") {
          sessionStorage.removeItem("token");
        }
      });
      return () => {
        authListener?.subscription?.unsubscribe?.();
      };
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navigate]);
  return null;
}

// APP SHELL - authenticated layout: Sidebar + TopBar + Outlet
function AppShell() {
  const navigate = useNavigate();
  // Passed to `ResponsiveGate` as a prop so that module stays router-free.
  const location = useLocation();

  const [toasts, setToasts] = useState([]);
  const addToast = useCallback((type, message) => {
    const id = crypto.randomUUID();
    setToasts(prev => [...prev, { id, type, message }]);
    return id;
  }, []);
  const removeToast = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  useEffect(() => {
    window.showToast = addToast;
    return () => { delete window.showToast; };
  }, [addToast]);

  useEffect(() => {
    const handleNavigate = (e) => {
      if (!e.detail) return;
      const target = navigationTargetForId(e.detail);
      // An unrecognised id does NOT navigate. The old `|| \`/app/${e.detail}\`` turned
      // every one into a push that the `/app/*` catch-all then bounced to the dashboard,
      // which reads to the user as the app deciding to go somewhere else on its own. A
      // console warning naming the id is the honest outcome: nothing moves, and the
      // caller that dispatched a dead id is identifiable.
      if (target === null) {
        console.warn(
          `[App] Ignoring 'navigate' event: "${e.detail}" is not a route id in `
            + 'components/shell/navigation.js.',
        );
        return;
      }
      navigate(target);
    };
    const handleAuthExpired = () => {
      sessionStorage.removeItem("token");
      navigate('/');
    };
    const handleStorageChange = (e) => {
      if (e.key === "token" && !e.newValue) navigate('/');
    };

    window.addEventListener('navigate', handleNavigate);
    window.addEventListener('auth-expired', handleAuthExpired);
    window.addEventListener('storage', handleStorageChange);
    return () => {
      window.removeEventListener('navigate', handleNavigate);
      window.removeEventListener('auth-expired', handleAuthExpired);
      window.removeEventListener('storage', handleStorageChange);
    };
  }, [navigate]);

  return (
    // The gate, not `DesktopOnlyOverlay`. `pathname` is a prop rather than a
    // `useLocation()` call inside the gate so that module needs no router; see
    // shell/ResponsiveGate.jsx. Below 768px it renders its own gate screen and
    // `children` never enter the tree, so nothing below here runs at all.
    <ResponsiveGate pathname={location.pathname}>
      {/* LEGAL-01: mounted inside the AUTHENTICATED shell, because
          /api/legal/acceptance requires a session — calling it from the public
          tree would 401 on every landing-page visit. Renders nothing unless the
          signed-in user's recorded acceptance is older than the published
          version, which is true for all 182 accounts that predate the
          acceptance table. */}
      <ReacceptanceGate />
      <ShellGrid toasts={toasts} removeToast={removeToast} />
    </ResponsiveGate>
  );
}

/**
 * The shell's CSS grid: sidebar column beside a top bar row and a scrolling main.
 *
 * SEPARATE COMPONENT ON PURPOSE. `useViewportAccess()` reads the context that
 * `ResponsiveGate` provides, and `AppShell` is the component that RENDERS the gate —
 * so it sits above the provider and would read `DEFAULT_CONTEXT`, silently pinning the
 * sidebar to the 216px expanded width at every viewport and losing the rail entirely.
 * This component is a child of the gate, so it reads the real decision.
 *
 * @param {Object} props
 * @param {Array} props.toasts
 * @param {(id: string) => void} props.removeToast
 */
function ShellGrid({ toasts, removeToast }) {
  // The tier decision, not a width: the breakpoint that chooses rail vs expanded is
  // `sidebarModeFor` in the gate, derived from `token.breakpoint.*` (Requirement 1.1).
  const { sidebarMode } = useViewportAccess();

  /*
    The product's single subscription from backend event to toast (§11.5). HERE and not in
    `AppShell` for one reason: below 768px `ResponsiveGate` renders its own screen and
    `ShellGrid` never enters the tree, so an unsupported viewport does not sit on the socket
    raising toasts over a gate screen that has no toast host to show them.

    It renders nothing and returns nothing — a frame must not re-render the shell. The toast
    host below is where a notification becomes visible, and it holds its own state.
  */
  useNotificationStream();

  return (
    <>
      {/* First focusable element in the shell, so a keyboard user reaches it before the
          sidebar's nav list. `sr-only` rather than `display: none` — a hidden-by-display
          link is not focusable at all, which is the bug this pattern exists to avoid —
          and `focus:not-sr-only` plus the fixed positioning below is what makes it
          actually visible once it takes focus. It targets `#main-content`, whose
          `tabIndex={-1}` is what lets the jump land focus in the content column. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:border focus:border-line-default focus:bg-surface-panel focus:px-4 focus:py-2 focus:text-small focus:font-medium focus:text-content-primary focus:outline-none focus:ring-2 focus:ring-brand"
      >
        Skip to main content
      </a>

      {/* `aria-live="polite"` belongs HERE and not inside `ToastContainer`: that
          primitive lives in ui-legacy/primitives.jsx and is shared with pages this
          task does not own, so adding the live region there would announce toasts on
          surfaces nobody has reviewed. Polite, not assertive — a toast is a report,
          not an interruption. */}
      <div aria-live="polite">
        <ToastContainer toasts={toasts} onRemove={removeToast} />
      </div>

      {/* No `fontFamily` on this wrapper. It used to carry
          `'IBM Plex Mono', 'Fira Code', monospace`, which made EVERY authenticated
          page render entirely in monospace. The shell now inherits `--font-sans`
          (Inter) from Tailwind's preflight, which reads `--font-sans` out of
          styles/tokens.css, and mono is applied locally — via `font-mono` — to
          numeric cells, identifiers, timestamps and code only (design.md §6.6,
          and what DESIGN_SYSTEM_V2.md already specified). Nothing may
          reintroduce a shell-wide mono default here. */}
      <div
        data-shell-grid=""
        className="bg-surface-canvas"
        style={{
          display: 'grid',
          // One track pair, so sidebar and content cannot disagree about the gutter the
          // way a flex `width` on one and `flex: 1` on the other could. The width comes
          // from the gate's table; `sidebarMode` is already 'expanded' | 'rail', and the
          // ternary is a total lookup rather than a trusting index.
          gridTemplateColumns: `${SIDEBAR_WIDTH_PX[sidebarMode === 'rail' ? 'rail' : 'expanded']}px 1fr`,
          // `min-content`, NOT `TOPBAR_HEIGHT_PX`. `TopBar` pins the bar row's height
          // inside its own <header> and renders a disconnected-state `Alert` strip below
          // that row; a fixed track would clip the strip exactly when it matters.
          gridTemplateRows: 'min-content 1fr',
          // `100%` rather than `100dvh` — the gate's outer element is already the
          // viewport height and this is the remainder after its restriction strip, so
          // `100dvh` here would overflow the document by the strip's height.
          height: '100%',
          width: '100%',
          minWidth: 0,
        }}
      >
        {/* No inline <style> here. The global `*` reset lives in the base-resets
            section of src/index.css and web-font loading lives in index.html's
            <head>; an inline <style> re-evaluates on every render of this
            component, and an @import inside one also blocks rendering
            (design.md §6.6). */}
        {/* Column 1, both rows: the sidebar runs the full height beside the bar, so the
            bar row starts at the content column rather than spanning the grid. */}
        <div style={{ gridColumn: 1, gridRow: '1 / -1', minWidth: 0, overflow: 'hidden' }}>
          {/* `accountMenu` is not optional in practice: without it the footer slot is an
              empty reserved 56px and the seven deferred routes and sign-out have no entry
              point anywhere in the app (§6.4). The menu's popover is portalled to
              `document.body` precisely because this wrapper and the sidebar itself both
              clip their overflow. */}
          <Sidebar accountMenu={<AccountMenu />} />
        </div>

        <div style={{ gridColumn: 2, gridRow: 1, minWidth: 0 }}>
          <TopBar />
        </div>

        {/* The one <main> landmark for authenticated pages — the content column was a
            bare <div> before, so there was no landmark to skip to. `min-width: 0` is
            what keeps a wide table inside its column instead of stretching the grid
            (Requirement 17.1); `min-height: 0` is the same guard vertically, without
            which the row refuses to shrink and the page scrolls the document.
            `scrollbar-gutter: stable` reserves the gutter so content does not shift
            sideways when a page's scrollbar appears. */}
        <main
          id="main-content"
          tabIndex={-1}
          // LEGAL-01 / data minimisation (GDPR Art 5(1)(c)).
          //
          // MASK EVERY AUTHENTICATED PAGE FROM MICROSOFT CLARITY SESSION REPLAY.
          //
          // Clarity records pointer movement, clicks and scrolling, and its
          // default masking covers form inputs but NOT page text. On a trading
          // platform the text is the sensitive part: account balances, position
          // sizes, P&L, strategy logic and exchange-connection screens all render
          // as ordinary text and would otherwise appear in a replay.
          //
          // Found by writing docs/legal/DPIA_SCREENING.md -- the screening
          // concluded a full DPIA is probably not MANDATORY, and then turned up
          // this, which is a real minimisation problem independent of that
          // question and fixable with an attribute rather than with legal
          // argument.
          //
          // Applied at the one <main> landmark for authenticated pages, so it
          // covers every route inside the shell by default rather than relying on
          // each new page remembering. The public marketing pages render outside
          // this element and stay unmasked, which is the point: they carry no
          // personal data and are where the analytics are actually useful.
          //
          // Clarity honours data-clarity-mask on a subtree. The ACCOUNT-WIDE
          // masking mode in the Clarity dashboard is a separate, owner-side
          // setting; this does not depend on it.
          data-clarity-mask="true"
          style={{
            gridColumn: 2,
            gridRow: 2,
            display: 'flex',
            flexDirection: 'column',
            minWidth: 0,
            minHeight: 0,
            overflowY: 'auto',
            scrollbarGutter: 'stable',
          }}
        >
          <Outlet />
        </main>
      </div>
    </>
  );
}

/**
 * What `/` serves — which is deliberately NOT the same in a browser and in the desktop app.
 *
 * The two are one bundle, built once, and they do not share a front door. A visitor who
 * types the domain has not been told what the product is, so the browser gets the marketing
 * landing page. Someone running the DOWNLOADED application has already been told: they
 * found it, downloaded it and installed it. Opening to a page that pitches the product and
 * offers four installers is a dead end — the desktop app would be advertising itself to a
 * user who is already inside it.
 *
 * So the desktop app opens at sign-in. `replace` rather than a push, so the landing page
 * never enters the desktop history stack: a Back press from the sign-in form must not land
 * on a marketing page with a "Download for Windows" button on it.
 *
 * A RETURNING DESKTOP USER DOES NOT SEE THE FORM. `/signin` sits behind `GuestGuard`, which
 * sends an already-authenticated session to `AUTH_REDIRECT_PATH`, so a session restored on
 * launch goes straight through to the dashboard. That is one redirect, before first paint,
 * and it is why this does not need to read the session itself.
 *
 * Exported for `tests/unit/desktopStartsAtSignIn.test.jsx`, which drives both platforms.
 */
export function RootRoute() {
  if (isDesktopApp()) return <Navigate to="/signin" replace />;

  return (
    <Suspense fallback={PUBLIC_FALLBACK}>
      <LandingPage />
    </Suspense>
  );
}

// ROOT ROUTING WRAPPER
export default function AppWrapper() {
  return (
    <ErrorBoundary>
      <AppStateProvider>
        <PasswordRecoveryHandler />
        {/* LEGAL-01: Google Analytics and Microsoft Clarity used to load on
            first paint, before any interaction, while the Privacy Policy
            claimed no third-party tracking was used. They are now gated on this
            banner's decision (src/legal/consent.js) and the Policy discloses
            them. Mounted outside <Routes> so the choice is offered on every
            page, including the legal documents themselves. */}
        <CookieConsent />
        <Routes>
          {/* Public landing. Every route from here to `/wizard` renders OUTSIDE the shell,
              so all of them take `PUBLIC_FALLBACK` — no page header, no route title. */}
          {/* Browser: the landing page. Desktop app: straight to sign-in. See `RootRoute`. */}
          <Route path="/" element={<RootRoute />} />
          <Route path="/download" element={<Suspense fallback={PUBLIC_FALLBACK}><DownloadPage /></Suspense>} />
          {/* Legal.
              LEGAL-01: `/legal` used to render a SECOND, different set of legal
              documents from src/pages/LegalPage.jsx, which contradicted the
              canonical ones on refunds — one promised pro-rated service credits,
              the other said all purchases were final. Which page a customer
              happened to open decided whether they were owed money. That file is
              deleted and this URL redirects, so there is one body of text behind
              one set of routes. A redirect rather than a removal keeps any
              existing link, bookmark or crawled URL working. */}
          <Route path="/legal" element={<Navigate to="/legal/terms" replace />} />
          <Route path="/legal/privacy" element={<Suspense fallback={PUBLIC_FALLBACK}><LegalPageRoute type="privacy" /></Suspense>} />
          <Route path="/legal/terms" element={<Suspense fallback={PUBLIC_FALLBACK}><LegalPageRoute type="terms" /></Suspense>} />
          <Route path="/legal/risk" element={<Suspense fallback={PUBLIC_FALLBACK}><LegalPageRoute type="risk" /></Suspense>} />
          <Route path="/legal/refund" element={<Suspense fallback={PUBLIC_FALLBACK}><LegalPageRoute type="refund" /></Suspense>} />

          {/* Marketplace accessible publicly */}
          <Route path="/marketplace" element={<Suspense fallback={PUBLIC_FALLBACK}><StrategyMarketplace /></Suspense>} />

          {/* Admin-only: AdminGuard verifies Supabase session + app_metadata.role === 'admin' */}
          <Route element={<AdminGuard />}>
            <Route path="/admin/waitlist" element={<Suspense fallback={PUBLIC_FALLBACK}><AdminDashboard /></Suspense>} />
          </Route>

          {/* Guest-only: redirect to /app/dashboard if already authenticated */}
          <Route element={<GuestGuard />}>
            <Route path="/signin" element={<Suspense fallback={PUBLIC_FALLBACK}><AuthPage mode="signin" /></Suspense>} />
            <Route path="/signup" element={<Suspense fallback={PUBLIC_FALLBACK}><AuthPage mode="signup" /></Suspense>} />
          </Route>

          {/* Password reset / onboarding (auth-agnostic) */}
          <Route path="/reset-password" element={<UpdatePasswordPage />} />
          <Route path="/2fa" element={<Suspense fallback={PUBLIC_FALLBACK}><TwoFA /></Suspense>} />
          <Route path="/wizard" element={<Suspense fallback={PUBLIC_FALLBACK}><Wizard /></Suspense>} />

          {/* Authenticated app shell */}
          <Route element={<AuthGuard />}>
            <Route element={<LoadingProvider><AppShell /></LoadingProvider>}>
              {/* Inside the shell, so every fallback here is `SHELL_FALLBACK` — the
                  reserved 64px `PageHeader` carrying this route's own title. */}
              <Route path="/app" element={<Navigate to="/app/dashboard" replace />} />
              <Route path="/app/dashboard" element={<Suspense fallback={SHELL_FALLBACK}><Dashboard /></Suspense>} />
              <Route path="/app/live-trading" element={<Suspense fallback={SHELL_FALLBACK}><LiveTrading /></Suspense>} />
              <Route path="/app/strategies" element={<Suspense fallback={SHELL_FALLBACK}><Strategies /></Suspense>} />
              <Route path="/app/strategies/:strategyId" element={<Suspense fallback={SHELL_FALLBACK}><StrategyDetail /></Suspense>} />
              <Route path="/app/signal-trace" element={<Suspense fallback={SHELL_FALLBACK}><SignalTrace /></Suspense>} />
              <Route path="/app/signal-trace/:signalId" element={<Suspense fallback={SHELL_FALLBACK}><SignalTrace /></Suspense>} />
              <Route path="/app/builder" element={<Suspense fallback={SHELL_FALLBACK}><StrategyBuilder /></Suspense>} />
              <Route path="/app/backtest" element={<Suspense fallback={SHELL_FALLBACK}><Backtester /></Suspense>} />
              <Route path="/app/backtester" element={<Suspense fallback={SHELL_FALLBACK}><Backtester /></Suspense>} />
              <Route path="/app/marketplace" element={<Suspense fallback={SHELL_FALLBACK}><StrategyMarketplace /></Suspense>} />
              <Route path="/app/exchange" element={<Suspense fallback={SHELL_FALLBACK}><ExchangeManager /></Suspense>} />
              <Route path="/app/risk" element={<Suspense fallback={SHELL_FALLBACK}><RiskSettings /></Suspense>} />
              <Route path="/app/billing" element={<Suspense fallback={SHELL_FALLBACK}><Billing /></Suspense>} />
              <Route path="/app/profile" element={<Suspense fallback={SHELL_FALLBACK}><Profile /></Suspense>} />
              <Route path="/app/security-logs" element={<Suspense fallback={SHELL_FALLBACK}><SecurityLogs /></Suspense>} />
              <Route path="/app/portfolio" element={<Suspense fallback={SHELL_FALLBACK}><Portfolio /></Suspense>} />
              <Route path="/app/trades" element={<Suspense fallback={SHELL_FALLBACK}><TradeHistory /></Suspense>} />
              <Route path="/app/paper-trading" element={<Suspense fallback={SHELL_FALLBACK}><PaperTrading /></Suspense>} />
              <Route path="/app/2fa" element={<Suspense fallback={SHELL_FALLBACK}><TwoFA /></Suspense>} />
              <Route path="/app/support" element={<SupportCenter />} />
              <Route path="/app/notifications" element={<NotificationCenter />} />
              <Route path="/app/*" element={<Navigate to="/app/dashboard" replace />} />
            </Route>
          </Route>

          {/* Global catch-all */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppStateProvider>
    </ErrorBoundary>
  );
}
