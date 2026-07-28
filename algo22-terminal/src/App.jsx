import React, { useState, useEffect, useCallback, lazy, Suspense } from "react";
import { Routes, Route, Navigate, Outlet, useNavigate, useLocation } from 'react-router-dom';
import * as Sentry from "@sentry/react";
import { CopilotProvider } from './contexts/CopilotContext';
import wsClient from './websocketClient';
import { Lock } from "lucide-react";
import ErrorBoundary from './components/ErrorBoundary';
import Sidebar from './components/Sidebar';
import TopBar from './components/TopBar';
import BotMonitoringConsole from './components/BotMonitoringConsole';
import SignalTraceVisualization from './components/SignalTraceVisualization';
import SupportPage from "./SupportPage";
import NotificationsPage from './components/NotificationsPage';
import CopilotChat from './components/CopilotChat';
import {
  C, Btn, Inp, ToastContainer,
  LoadingProvider,
} from './components/ui-legacy/primitives';

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
const StrategyBuilder     = lazy(() => import('./pages/StrategyBuilder'));
const Backtester          = lazy(() => import('./pages/Backtester'));
const StrategyMarketplace = lazy(() => import('./pages/StrategyMarketplace'));
const ExchangeManager     = lazy(() => import('./pages/ExchangeManager'));
const RiskSettings        = lazy(() => import('./pages/RiskSettings'));
const Billing             = lazy(() => import('./pages/Billing'));
const Leaderboard         = lazy(() => import('./pages/Leaderboard'));
const Referral            = lazy(() => import('./pages/Referral'));
const Profile             = lazy(() => import('./pages/Profile'));
const SecurityLogs        = lazy(() => import('./pages/SecurityLogs'));

const PAGE_FALLBACK = <div style={{ background: '#080A0E', minHeight: '100vh' }} />;
const TENANT_ID = "default";

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
            <Btn v="primary" sz="md" cls="w-full justify-center mt-2" disabled={loading}>
              {loading ? "Updating..." : "Update Password"}
            </Btn>
            {!!error && (
              <div style={{ marginTop: 10, color: C.red, fontSize: 12, textAlign: "center" }}>{error}</div>
            )}
          </form>
        )}
      </div>
    </div>
  );
}

// AUTH GUARDS
function AuthGuard() {
  const token = sessionStorage.getItem("token");
  if (!token) return <Navigate to="/signin" replace />;
  return <Outlet />;
}

function GuestGuard() {
  const token = sessionStorage.getItem("token");
  if (token) return <Navigate to="/app/dashboard" replace />;
  return <Outlet />;
}

// Intercepts Supabase password-recovery hash on mount
function PasswordRecoveryHandler() {
  const navigate = useNavigate();
  useEffect(() => {
    const hash = window.location.hash;
    if (hash && hash.includes("type=recovery")) {
      const params = new URLSearchParams(hash.substring(1));
      const token = params.get("access_token");
      if (token) {
        sessionStorage.setItem("token", token);
        window.history.replaceState(null, "", window.location.pathname);
        navigate("/reset-password", { replace: true });
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

// APP SHELL - authenticated layout: Sidebar + TopBar + Outlet
function AppShell() {
  const navigate = useNavigate();

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
    const PATH_MAP = {
      dashboard: '/app/dashboard', strategies: '/app/strategies',
      builder: '/app/builder', backtest: '/app/backtest',
      marketplace: '/app/marketplace', exchange: '/app/exchange',
      risk: '/app/risk', billing: '/app/billing',
      leaderboard: '/app/leaderboard', referral: '/app/referral',
      profile: '/app/profile', support: '/app/support',
      notifications: '/app/notifications', 'bot-monitor': '/app/bot-monitor',
      'signal-trace': '/app/signal-trace', 'security-logs': '/app/security-logs',
      landing: '/',
    };

    const handleNavigate = (e) => {
      if (!e.detail) return;
      navigate(PATH_MAP[e.detail] || `/app/${e.detail}`);
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
    <div style={{ background: C.bg0, minHeight: "100vh", display: "flex", fontFamily: "'IBM Plex Mono', 'Fira Code', monospace" }}>
      <ToastContainer toasts={toasts} onRemove={removeToast} />
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
      `}</style>
      <Sidebar />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, overflow: "hidden" }}>
        <TopBar />
        <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column" }}>
          <Outlet />
        </div>
        <CopilotChat />
      </div>
    </div>
  );
}

// ROOT ROUTING WRAPPER
export default function AppWrapper() {
  return (
    <ErrorBoundary>
      <CopilotProvider>
        <PasswordRecoveryHandler />
        <Routes>
          {/* Public landing */}
          <Route path="/" element={<Suspense fallback={PAGE_FALLBACK}><LandingPage /></Suspense>} />
          <Route path="/download" element={<Suspense fallback={PAGE_FALLBACK}><DownloadPage /></Suspense>} />
          <Route path="/admin/waitlist" element={<Suspense fallback={PAGE_FALLBACK}><AdminDashboard /></Suspense>} />

          {/* Legal */}
          <Route path="/legal" element={<Suspense fallback={PAGE_FALLBACK}><LegalPage /></Suspense>} />
          <Route path="/legal/privacy" element={<Suspense fallback={PAGE_FALLBACK}><LegalPageRoute type="privacy" /></Suspense>} />
          <Route path="/legal/terms" element={<Suspense fallback={PAGE_FALLBACK}><LegalPageRoute type="terms" /></Suspense>} />
          <Route path="/legal/risk" element={<Suspense fallback={PAGE_FALLBACK}><LegalPageRoute type="risk" /></Suspense>} />
          <Route path="/legal/refund" element={<Suspense fallback={PAGE_FALLBACK}><LegalPageRoute type="refund" /></Suspense>} />

          {/* Marketplace accessible publicly */}
          <Route path="/marketplace" element={<Suspense fallback={PAGE_FALLBACK}><StrategyMarketplace /></Suspense>} />

          {/* Guest-only: redirect to /app/dashboard if already authenticated */}
          <Route element={<GuestGuard />}>
            <Route path="/signin" element={<Suspense fallback={PAGE_FALLBACK}><AuthPage mode="signin" /></Suspense>} />
            <Route path="/signup" element={<Suspense fallback={PAGE_FALLBACK}><AuthPage mode="signup" /></Suspense>} />
          </Route>

          {/* Password reset / onboarding (auth-agnostic) */}
          <Route path="/reset-password" element={<UpdatePasswordPage />} />
          <Route path="/2fa" element={<Suspense fallback={PAGE_FALLBACK}><TwoFA /></Suspense>} />
          <Route path="/wizard" element={<Suspense fallback={PAGE_FALLBACK}><Wizard /></Suspense>} />

          {/* Authenticated app shell */}
          <Route element={<AuthGuard />}>
            <Route element={<LoadingProvider><AppShell /></LoadingProvider>}>
              <Route path="/app" element={<Navigate to="/app/dashboard" replace />} />
              <Route path="/app/dashboard" element={<Suspense fallback={PAGE_FALLBACK}><Dashboard /></Suspense>} />
              <Route path="/app/strategies" element={<Suspense fallback={PAGE_FALLBACK}><Strategies /></Suspense>} />
              <Route path="/app/builder" element={<Suspense fallback={PAGE_FALLBACK}><StrategyBuilder /></Suspense>} />
              <Route path="/app/backtest" element={<Suspense fallback={PAGE_FALLBACK}><Backtester /></Suspense>} />
              <Route path="/app/marketplace" element={<Suspense fallback={PAGE_FALLBACK}><StrategyMarketplace /></Suspense>} />
              <Route path="/app/bot-monitor" element={<BotMonitoringConsole wsClient={wsClient} accountId={TENANT_ID} />} />
              <Route path="/app/signal-trace" element={<SignalTraceVisualization wsClient={wsClient} accountId={TENANT_ID} />} />
              <Route path="/app/exchange" element={<Suspense fallback={PAGE_FALLBACK}><ExchangeManager /></Suspense>} />
              <Route path="/app/risk" element={<Suspense fallback={PAGE_FALLBACK}><RiskSettings /></Suspense>} />
              <Route path="/app/billing" element={<Suspense fallback={PAGE_FALLBACK}><Billing /></Suspense>} />
              <Route path="/app/leaderboard" element={<Suspense fallback={PAGE_FALLBACK}><Leaderboard /></Suspense>} />
              <Route path="/app/referral" element={<Suspense fallback={PAGE_FALLBACK}><Referral /></Suspense>} />
              <Route path="/app/profile" element={<Suspense fallback={PAGE_FALLBACK}><Profile /></Suspense>} />
              <Route path="/app/security-logs" element={<Suspense fallback={PAGE_FALLBACK}><SecurityLogs /></Suspense>} />
              <Route path="/app/support" element={<SupportPage />} />
              <Route path="/app/notifications" element={<NotificationsPage />} />
              <Route path="/app/*" element={<Navigate to="/app/dashboard" replace />} />
            </Route>
          </Route>

          {/* Global catch-all */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </CopilotProvider>
    </ErrorBoundary>
  );
}
