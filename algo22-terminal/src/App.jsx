import React, { useState, useEffect, useCallback, lazy, Suspense } from "react";
import { Routes, Route, Navigate, Outlet, useNavigate, useLocation } from 'react-router-dom';
import * as Sentry from "@sentry/react";
import { AppStateProvider } from './AppState';
import wsClient from './websocketClient';
import { Lock } from "lucide-react";
import { supabase } from './supabase';
import ErrorBoundary from './components/ErrorBoundary';
import Sidebar from './components/Sidebar';
import TopBar from './components/TopBar';
import SupportCenter from './components/SupportCenter';
import NotificationCenter from './components/NotificationCenter';
// `DesktopOnlyOverlay` is no longer imported — the shell is gated by
// `shell/ResponsiveGate` instead (task 8.4/8.5). The component FILE still exists;
// task 27.3 owns deleting it. Keeping a dead import here would only trip no-unused-vars.
import ResponsiveGate, { SIDEBAR_WIDTH_PX, useViewportAccess } from './components/shell/ResponsiveGate';
// The sidebar's footer slot (task 8.8). It is the app's ONLY route to the seven pages
// §6.4 defers out of primary nav — profile, security log, billing, exchanges, risk,
// notifications, support — and to sign-out, so `Sidebar` is never rendered without it.
import AccountMenu from './components/shell/AccountMenu';
// The one route/nav table (task 8.1). Both the `Suspense` fallback's title and the
// `'navigate'` bridge's id resolution read from it, so neither can disagree with the
// sidebar about what a route is called or where an id points.
import { NAV_ENTRIES, routeTitle } from './components/shell/navigation';
// Direct module paths, not `components/ds/index.js`: the barrel pulls the module graph
// of every primitive, and the shell needs exactly these two (design.md §26).
import { PageHeader } from './components/ds/PageHeader';
import { LoadingState } from './components/ds/LoadingState';
import {
  C, Inp, ToastContainer,
  LoadingProvider,
} from './components/ui-legacy/primitives';
import { Button } from './components/ui/Button';

// Public routes (lazy)
const LandingPage    = lazy(() => import('./components/landing/LandingPage'));
const DownloadPage   = lazy(() => import('./components/download/DownloadPage'));
const AdminDashboard = lazy(() => import('./components/admin/AdminDashboard'));
const LegalPageRoute = lazy(() => import('./components/legal/LegalPage'));

// Auth / onboarding pages (lazy)
const AuthPage  = lazy(() => import('./pages/AuthPage'));
const TwoFA     = lazy(() => import('./pages/TwoFA'));
const Wizard    = lazy(() => import('./pages/Wizard'));
const LegalPage = lazy(() => import('./pages/LegalPage'));

// Authenticated app pages (lazy - one chunk per route)
const Dashboard           = lazy(() => import('./pages/Dashboard'));
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
      const { error: supaError } = await supabase.auth.updateUser({ password });
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
    <div style={{ background: C.bg0, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 18, padding: 36, width: 450 }}>
        <h1 style={{ color: C.t1, fontWeight: 900, fontSize: 22, marginBottom: 20, textAlign: "center" }}>
          Reset Password
        </h1>
        {success ? (
          <div style={{ color: C.green, fontSize: 13, textAlign: "center", background: `${C.green}12`, padding: 12, borderRadius: 8 }}>
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
              <div style={{ marginTop: 10, color: C.red, fontSize: 12, textAlign: "center" }}>{error}</div>
            )}
          </form>
        )}
      </div>
    </div>
  );
}

// Synchronously extracts auth token from sessionStorage or URL hash
function getEffectiveToken() {
  let token = sessionStorage.getItem("token");
  if (!token && typeof window !== "undefined" && window.location.hash) {
    const hash = window.location.hash;
    if (hash.includes("access_token=")) {
      const params = new URLSearchParams(hash.startsWith("#") ? hash.substring(1) : hash);
      const hashToken = params.get("access_token");
      if (hashToken) {
        sessionStorage.setItem("token", hashToken);
        token = hashToken;
      }
    }
  }
  return token;
}

// AUTH GUARDS
function AuthGuard() {
  const token = getEffectiveToken();
  if (!token) return <Navigate to="/signin" replace />;
  return <Outlet />;
}

function GuestGuard() {
  const token = getEffectiveToken();
  if (token) return <Navigate to="/app/dashboard" replace />;
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
      <div style={{ background: C.bg0, minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {/* `font-mono` rather than the bare `monospace` keyword this carried before:
            the keyword resolves to whatever the browser defaults to, while the class
            resolves `--font-mono` and so actually reaches the JetBrains Mono that
            index.html loads. */}
        <span className="font-mono" style={{ color: C.t3, fontSize: 12 }}>Verifying access…</span>
      </div>
    );
  }
  if (status === 'denied') {
    return (
      <div style={{ background: C.bg0, minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
        <Lock size={32} style={{ color: C.red }} />
        <div style={{ color: C.t1, fontWeight: 700, fontSize: 16 }}>Access Denied</div>
        {/* Same correction as the loading branch above: the real mono stack, not the
            bare `monospace` keyword. */}
        <div className="font-mono" style={{ color: C.t3, fontSize: 11 }}>Administrator credentials required.</div>
      </div>
    );
  }
  return <Outlet />;
}

// Intercepts Supabase OAuth and password-recovery hashes on mount
function PasswordRecoveryHandler() {
  const navigate = useNavigate();
  useEffect(() => {
    const hash = window.location.hash;
    if (hash) {
      if (hash.includes("type=recovery")) {
        const params = new URLSearchParams(hash.startsWith("#") ? hash.substring(1) : hash);
        const token = params.get("access_token");
        if (token) {
          sessionStorage.setItem("token", token);
          window.history.replaceState(null, "", window.location.pathname);
          navigate("/reset-password", { replace: true });
        }
      } else if (hash.includes("access_token=")) {
        const params = new URLSearchParams(hash.startsWith("#") ? hash.substring(1) : hash);
        const token = params.get("access_token");
        if (token) {
          sessionStorage.setItem("token", token);
          window.history.replaceState(null, "", window.location.pathname);
          if (window.location.pathname === "/signin" || window.location.pathname === "/signup" || window.location.pathname === "/") {
            navigate("/app/dashboard", { replace: true });
          }
        }
      }
    }

    // Subscribe to onAuthStateChange for live session events
    if (supabase?.auth?.onAuthStateChange) {
      const { data: authListener } = supabase.auth.onAuthStateChange((event, session) => {
        if (session?.access_token && (sessionStorage.getItem("token") || event === "SIGNED_IN")) {
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

// ROOT ROUTING WRAPPER
export default function AppWrapper() {
  return (
    <ErrorBoundary>
      <AppStateProvider>
        <PasswordRecoveryHandler />
        <Routes>
          {/* Public landing. Every route from here to `/wizard` renders OUTSIDE the shell,
              so all of them take `PUBLIC_FALLBACK` — no page header, no route title. */}
          <Route path="/" element={<Suspense fallback={PUBLIC_FALLBACK}><LandingPage /></Suspense>} />
          <Route path="/download" element={<Suspense fallback={PUBLIC_FALLBACK}><DownloadPage /></Suspense>} />
          {/* Legal */}
          <Route path="/legal" element={<Suspense fallback={PUBLIC_FALLBACK}><LegalPage /></Suspense>} />
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
              <Route path="/app/live-trading" element={<Suspense fallback={SHELL_FALLBACK}><Dashboard /></Suspense>} />
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
