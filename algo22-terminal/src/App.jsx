import React, { useState, useEffect, useRef, useCallback, useMemo, createContext, useContext, lazy, Suspense } from "react";
import { Routes, Route, useLocation, Navigate } from 'react-router-dom';

// ── Landing page components (lazy loaded for web/Vercel deployment) ──
const LandingPage = lazy(() => import('./components/landing/LandingPage'));
const DownloadPage = lazy(() => import('./components/download/DownloadPage'));
const AdminDashboard = lazy(() => import('./components/admin/AdminDashboard'));
const LegalPageRoute = lazy(() => import('./components/legal/LegalPage'));

import * as Sentry from "@sentry/react";
import { CopilotProvider } from './contexts/CopilotContext';
import { api, endpoints, get, post } from './api';
import wsClient from './websocketClient';
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  useReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  applyEdgeChanges,
} from "reactflow";
import "reactflow/dist/style.css";
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, ReferenceLine, ComposedChart, Scatter
} from "recharts";
// lightweight-charts imports removed - only used in disabled LiveTrading page
// import { createChart, ColorType, CandlestickSeries, HistogramSeries } from "lightweight-charts";
import {
  LayoutDashboard, TrendingUp, Bot, Link2, Bell, Shield,
  LogOut, ChevronDown, ChevronRight, Search,
  Plus, Edit2, Trash2, Play, BarChart2, Zap, Lock,
  AlertTriangle, CheckCircle, XCircle, Globe, Users,
  CreditCard, FileText, HelpCircle, Activity,
  Eye, EyeOff, Copy, RefreshCw, ArrowUpRight,
  ArrowDownRight, Filter, Download, Star, ArrowLeft,
  Award, Gift, BookOpen, MessageSquare, Volume2,
  Cpu, HardDrive, Wifi, Database, Layers, DollarSign,
  PieChart as PieIcon, Calendar, Sliders, Key,
  UserCheck, AlertCircle, Clock, Target, Flame,
  TrendingDown, Percent, Hash, Mail,
  ShieldCheck, Smartphone, RotateCcw, Send, PlusCircle,
  Radio, Pause, StopCircle, MoreHorizontal, Check,
  ChevronUp, ExternalLink, Info, Minus, Triangle,
  Hexagon, Crosshair, Gauge, Maximize2, Minimize2,
  Package, Tag, Bookmark, Flag, Rss,
  Brain, GitBranch, Server, WifiOff, Loader2
} from "lucide-react";
import SupportPage from "./SupportPage";
import NotificationsPage from './components/NotificationsPage';
import DrawdownMonitor, { DRAWDOWN_LEVELS } from './components/DrawdownMonitor';
import KillSwitchBanner from './components/KillSwitchBanner';
import LiveRiskAlerts from './components/LiveRiskAlerts';
import StrategyDashboard from './components/StrategyDashboard';
import SignalTracePanel from './components/SignalTracePanel';
import BotMonitoringConsole from './components/BotMonitoringConsole';
import SignalTraceVisualization from './components/SignalTraceVisualization';
import InfrastructureOperations from './components/InfrastructureOperations';
import {
  EquityCurveChart,
  DrawdownChart,
  LivePositions,
  PerformanceMetrics,
  SystemStatus,
  RiskAlertBanner
} from './components/DashboardUpgrades';

// ═══════════════════════════════════════════════════════════════════
//  ERROR BOUNDARY
// ═══════════════════════════════════════════════════════════════════

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
    this.setState({ errorInfo });
    Sentry.captureException(error, { extra: errorInfo });
  }

  handleCopyError = () => {
    const errorText = `Error: ${this.state.error?.message || 'Unknown error'}\n\nStack:\n${this.state.error?.stack || 'No stack trace'}\n\nComponent Stack:\n${this.state.errorInfo?.componentStack || 'No component stack'}`;
    navigator.clipboard.writeText(errorText);
  };

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          background: "#010608",
          color: "#6b9bb8",
          fontFamily: "monospace",
          padding: 40
        }}>
          <div style={{ fontSize: 64, marginBottom: 20 }}>⚠️</div>
          <div style={{ fontSize: 20, fontWeight: 900, marginBottom: 8, color: "#00d4ff" }}>
            Application Error
          </div>
          <div style={{ fontSize: 12, marginBottom: 24, textAlign: "center", maxWidth: 500, lineHeight: 1.6 }}>
            An unexpected error occurred. The application has been prevented from crashing to preserve your data.
          </div>

          {/* Error Details */}
          <div style={{
            background: "#0a1014",
            border: "1px solid #1a2530",
            borderRadius: 8,
            padding: 16,
            maxWidth: 600,
            width: "100%",
            marginBottom: 20,
            maxHeight: 300,
            overflow: "auto"
          }}>
            <div style={{ fontSize: 10, fontWeight: 700, marginBottom: 8, color: "#ff4757" }}>
              ERROR DETAILS
            </div>
            <div style={{ fontSize: 10, color: "#ff6b81", marginBottom: 12, wordBreak: "break-word" }}>
              {this.state.error?.message || "Unknown error"}
            </div>

            {this.state.error?.stack && (
              <div style={{ fontSize: 9, color: "#4a5568", marginBottom: 12, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {this.state.error.stack}
              </div>
            )}

            {this.state.errorInfo?.componentStack && (
              <div style={{ fontSize: 9, color: "#4a5568", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {this.state.errorInfo.componentStack}
              </div>
            )}
          </div>

          {/* Action Buttons */}
          <div style={{ display: "flex", gap: 12 }}>
            <button
              onClick={() => window.location.reload()}
              style={{
                background: "#00d4ff22",
                border: "1px solid #00d4ff55",
                color: "#00d4ff",
                borderRadius: 6,
                padding: "10px 20px",
                fontSize: 11,
                fontWeight: 700,
                cursor: "pointer",
                fontFamily: "monospace",
                transition: "all 0.2s"
              }}
              onMouseOver={(e) => e.target.style.background = "#00d4ff33"}
              onMouseOut={(e) => e.target.style.background = "#00d4ff22"}
            >
              Reload Page
            </button>

            <button
              onClick={this.handleCopyError}
              style={{
                background: "#1a2530",
                border: "1px solid #2d3b4a",
                color: "#6b9bb8",
                borderRadius: 6,
                padding: "10px 20px",
                fontSize: 11,
                fontWeight: 700,
                cursor: "pointer",
                fontFamily: "monospace",
                transition: "all 0.2s"
              }}
              onMouseOver={(e) => e.target.style.background = "#2d3b4a"}
              onMouseOut={(e) => e.target.style.background = "#1a2530"}
            >
              Copy Error
            </button>
          </div>

          <div style={{ fontSize: 10, marginTop: 16, color: "#4a5568" }}>
            Timestamp: {new Date().toISOString()}
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// ═══════════════════════════════════════════════════════════════════
//  DESIGN SYSTEM - PROFESSIONAL TRADING THEME
// ═══════════════════════════════════════════════════════════════════
const C = {
  // Background hierarchy
  bg: "#0B0F14",   // Main background
  bg0: "#0B0F14",   // Base dark
  bg1: "#0E1319",   // Slightly elevated
  bg2: "#12181F",   // Panel background  
  bg3: "#161D26",   // Input/Card backgrounds
  bg4: "#1A222C",   // Hover states

  // Border system - subtle and professional
  border: "#1E2733",  // Main border
  borderLight: "#2A3441",
  borderHover: "#3D4D5C",

  // Accent colors - minimal, professional
  accent: "#2962FF",  // Primary accent (blue)
  accentHover: "#448AFF",
  accentMuted: "#1E40AF",

  // Profit/Loss - standard trading colors
  profit: "#00C853",  // Green for profit
  profitDark: "#00B248",
  profitBg: "rgba(0, 200, 83, 0.1)",
  loss: "#FF3D00",  // Red for loss
  lossDark: "#DD2C00",
  lossBg: "rgba(255, 61, 0, 0.1)",

  // Text hierarchy
  t1: "#E6EDF3",  // Primary text
  t2: "#8B949E",  // Secondary text
  t3: "#64748B",  // Muted text
  t4: "#475569",  // Disabled/hint text

  // Utility colors
  warning: "#FFAB00",
  gold: "#FFD600",
  purple: "#7C4DFF",

  // Shadows
  shadow: "0 2px 4px rgba(0,0,0,0.2)",
  shadowMd: "0 4px 8px rgba(0,0,0,0.3)",
  shadowLg: "0 8px 24px rgba(0,0,0,0.4)",

  // Premium Glow Effects
  glow: {
    profit: "0 0 20px rgba(0, 200, 83, 0.3), 0 0 40px rgba(0, 200, 83, 0.1)",
    loss: "0 0 20px rgba(255, 61, 0, 0.3), 0 0 40px rgba(255, 61, 0, 0.1)",
    accent: "0 0 20px rgba(41, 98, 255, 0.3), 0 0 40px rgba(41, 98, 255, 0.1)",
    warning: "0 0 20px rgba(255, 171, 0, 0.3), 0 0 40px rgba(255, 171, 0, 0.1)",
    gold: "0 0 20px rgba(255, 214, 0, 0.3), 0 0 40px rgba(255, 214, 0, 0.1)",
    purple: "0 0 20px rgba(124, 77, 255, 0.3), 0 0 40px rgba(124, 77, 255, 0.1)",
  },

  // Gradient overlays for depth
  gradient: {
    profit: "linear-gradient(135deg, rgba(0,200,83,0.15) 0%, rgba(0,200,83,0.05) 100%)",
    loss: "linear-gradient(135deg, rgba(255,61,0,0.15) 0%, rgba(255,61,0,0.05) 100%)",
    accent: "linear-gradient(135deg, rgba(41,98,255,0.15) 0%, rgba(41,98,255,0.05) 100%)",
    card: "linear-gradient(180deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0) 100%)",
    shine: "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.1) 50%, transparent 100%)",
  },

  // Spacing
  space: {
    xs: 4, sm: 8, md: 12,
    lg: 16, xl: 20, xxl: 24
  },

  // Border radius
  radius: {
    sm: 4, md: 6, lg: 8, xl: 12
  },

  // Legacy aliases for backward compatibility
  cyan: "#2962FF",
  cyanL: "#448AFF",
  cyanD: "#1E40AF",
  green: "#00C853",
  greenD: "#00B248",
  red: "#FF3D00",
  orange: "#FFAB00",
};


// ── Micro Components ────────────────────────────────────────
const Tag2 = ({ c = "accent", children, interactive = false }) => {
  const [isHovered, setIsHovered] = useState(false);
  const m = {
    accent: { bg: C.bg3, border: C.borderLight, text: C.accent, hoverBg: C.bg4 },
    profit: { bg: C.profitBg, border: "rgba(0,200,83,0.3)", text: C.profit, hoverBg: "rgba(0,200,83,0.15)" },
    loss: { bg: C.lossBg, border: "rgba(255,61,0,0.3)", text: C.loss, hoverBg: "rgba(255,61,0,0.15)" },
    warning: { bg: "rgba(255,171,0,0.1)", border: "rgba(255,171,0,0.3)", text: C.warning, hoverBg: "rgba(255,171,0,0.15)" },
    purple: { bg: "rgba(124,77,255,0.1)", border: "rgba(124,77,255,0.3)", text: C.purple, hoverBg: "rgba(124,77,255,0.15)" },
    gold: { bg: "rgba(255,214,0,0.1)", border: "rgba(255,214,0,0.3)", text: C.gold, hoverBg: "rgba(255,214,0,0.15)" },
    cyan: { bg: C.bg3, border: C.borderLight, text: C.accent, hoverBg: C.bg4 },
    green: { bg: C.profitBg, border: "rgba(0,200,83,0.3)", text: C.profit, hoverBg: "rgba(0,200,83,0.15)" },
    red: { bg: C.lossBg, border: "rgba(255,61,0,0.3)", text: C.loss, hoverBg: "rgba(255,61,0,0.15)" },
  };
  const style = m[c] || m.accent;
  return (
    <span
      onMouseEnter={() => interactive && setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        background: isHovered && interactive ? style.hoverBg : style.bg,
        border: `1px solid ${isHovered && interactive ? style.text : style.border}`,
        color: style.text,
        boxShadow: isHovered && interactive ? C.shadowMd : C.shadow,
        borderRadius: C.radius.sm,
        padding: "2px 8px",
        fontSize: 9,
        fontFamily: "monospace",
        fontWeight: 700,
        letterSpacing: 0.5,
        textTransform: "uppercase",
        display: "inline-flex",
        alignItems: "center",
        cursor: interactive ? "pointer" : "default",
        transform: isHovered && interactive ? "scale(1.02)" : "none",
        transition: "all 0.15s cubic-bezier(0.4, 0, 0.2, 1)"
      }}
    >
      {children}
    </span>
  );
};

const Btn = ({ children, v = "primary", sz = "md", Icon, onClick, disabled, cls = "" }) => {
  const V = {
    primary: {
      background: C.accent,
      color: "#fff",
      fontWeight: 600,
      border: "none",
      boxShadow: C.shadow,
      hoverBg: C.accentHover,
      hoverTransform: "translateY(-1px)",
      activeTransform: "translateY(0) scale(0.98)"
    },
    outline: {
      background: "transparent",
      border: `1px solid ${C.border}`,
      color: C.accent,
      hoverBg: C.bg3,
      hoverBorder: C.borderLight,
      hoverTransform: "translateY(-1px)",
      activeTransform: "translateY(0) scale(0.98)"
    },
    ghost: {
      background: "transparent",
      border: "none",
      color: C.t2,
      hoverBg: C.bg3,
      hoverColor: C.t1,
      hoverTransform: "none",
      activeTransform: "scale(0.98)"
    },
    danger: {
      background: C.lossBg,
      border: `1px solid rgba(239,68,68,0.3)`,
      color: C.loss,
      hoverBg: "rgba(239,68,68,0.2)",
      hoverTransform: "translateY(-1px)",
      activeTransform: "translateY(0) scale(0.98)"
    },
    success: {
      background: C.profitBg,
      border: `1px solid rgba(34,197,94,0.3)`,
      color: C.profit,
      hoverBg: "rgba(34,197,94,0.2)",
      hoverTransform: "translateY(-1px)",
      activeTransform: "translateY(0) scale(0.98)"
    },
    gold: {
      background: C.gold,
      color: "#000",
      fontWeight: 600,
      border: "none",
      boxShadow: C.shadow,
      hoverBg: "#FCD34D",
      hoverTransform: "translateY(-1px)",
      activeTransform: "translateY(0) scale(0.98)"
    },
    red: {
      background: C.loss,
      color: "#fff",
      fontWeight: 600,
      border: "none",
      boxShadow: C.shadow,
      hoverBg: C.lossDark,
      hoverTransform: "translateY(-1px)",
      activeTransform: "translateY(0) scale(0.98)"
    },
  };
  const S = {
    xs: { padding: "4px 8px", fontSize: "9px" },
    sm: { padding: "5px 12px", fontSize: "10px" },
    md: { padding: "7px 16px", fontSize: "12px" },
    lg: { padding: "10px 24px", fontSize: "14px" }
  };
  const icon = Icon ? <Icon size={sz === "xs" ? 9 : sz === "sm" ? 11 : 13} /> : null;
  const variant = V[v];
  const [isHovered, setIsHovered] = useState(false);
  const [isPressed, setIsPressed] = useState(false);

  return (
    <button
      disabled={disabled}
      onClick={onClick}
      tabIndex={disabled ? -1 : 0}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => { setIsHovered(false); setIsPressed(false); }}
      onMouseDown={() => setIsPressed(true)}
      onMouseUp={() => setIsPressed(false)}
      style={{
        ...S[sz],
        background: isHovered && variant.hoverBg ? variant.hoverBg : variant.background,
        border: isHovered && variant.hoverBorder ? `1px solid ${variant.hoverBorder}` : variant.border,
        color: isHovered && variant.hoverColor ? variant.hoverColor : variant.color,
        fontWeight: variant.fontWeight,
        boxShadow: isHovered ? C.shadowMd : variant.boxShadow,
        borderRadius: C.radius.md,
        fontFamily: "monospace",
        letterSpacing: 0.5,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 6,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.4 : 1,
        transform: isPressed ? variant.activeTransform : (isHovered ? variant.hoverTransform : "none"),
        transition: "all 0.15s cubic-bezier(0.4, 0, 0.2, 1)"
      }}
      className={cls}
    >
      {icon}{children}
    </button>
  );
};

const Inp = ({ lbl, ph, type = "text", icon: Icon, hint, val, onChange, disabled = false }) => {
  const [isFocused, setIsFocused] = useState(false);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: C.space.xs }}>
      {lbl && <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 700, letterSpacing: 1.5, textTransform: "uppercase" }}>{lbl}</label>}
      <div style={{ position: "relative" }}>
        {Icon && <Icon size={12} style={{ color: isFocused ? C.accent : C.t3, position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", transition: "color 0.15s ease" }} />}
        <input type={type} value={val} onChange={onChange} placeholder={ph} disabled={disabled}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          style={{
            background: C.bg3,
            border: `1px solid ${isFocused ? C.accent : C.border}`,
            color: C.t1,
            paddingLeft: Icon ? 32 : 12,
            paddingRight: 12,
            paddingTop: 8,
            paddingBottom: 8,
            opacity: disabled ? 0.45 : 1,
            cursor: disabled ? "not-allowed" : "text",
            borderRadius: C.radius.md,
            fontSize: 12,
            fontFamily: "monospace",
            width: "100%",
            outline: "none",
            boxShadow: isFocused ? `0 0 0 3px ${C.accent}20` : "none",
            transition: "all 0.15s cubic-bezier(0.4, 0, 0.2, 1)"
          }}
        />
      </div>
      {hint && <p style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }}>{hint}</p>}
    </div>
  );
};

const Card = ({ children, cls = "", style = {}, hover = false, elevate = false }) => {
  const [isHovered, setIsHovered] = useState(false);
  return (
    <div
      onMouseEnter={() => hover && setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        background: C.bg2,
        border: `1px solid ${isHovered ? C.borderLight : C.border}`,
        borderRadius: C.radius.lg,
        boxShadow: isHovered && elevate ? C.shadowMd : C.shadow,
        transform: isHovered && elevate ? "translateY(-2px)" : "none",
        transition: "all 0.15s cubic-bezier(0.4, 0, 0.2, 1)",
        ...style
      }}
      className={cls}
    >
      {children}
    </div>
  );
};

const SectionH = ({ title, sub, right }) => (
  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: C.space.lg }}>
    <div>
      <h2 style={{ color: C.t1, fontSize: 18, fontWeight: 800, letterSpacing: -0.5 }}>{title}</h2>
      {sub && <p style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", marginTop: C.space.xs }}>{sub}</p>}
    </div>
    {right}
  </div>
);

const PanelTitle = ({ title, sub, right }) => (
  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: C.space.md }}>
    <div>
      <h3 style={{ color: C.t1, fontSize: 13, fontWeight: 700, letterSpacing: -0.3 }}>{title}</h3>
      {sub && <p style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", marginTop: C.space.xs }}>{sub}</p>}
    </div>
    {right}
  </div>
);

// ── Table Components ────────────────────────────────────────
const Table = ({ children, cls = "" }) => (
  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, fontFamily: "monospace" }} className={cls}>
    {children}
  </table>
);

const TableHead = ({ children }) => (
  <thead>
    <tr style={{ borderBottom: `1px solid ${C.border}` }}>
      {children}
    </tr>
  </thead>
);

const TableHeader = ({ children, align = "left" }) => (
  <th style={{
    color: C.t3,
    fontWeight: 700,
    padding: "10px 12px",
    textAlign: align,
    fontSize: 9,
    letterSpacing: 1,
    textTransform: "uppercase"
  }}>
    {children}
  </th>
);

const TableRow = ({ children, highlight = false, interactive = false }) => {
  const [isHovered, setIsHovered] = useState(false);
  return (
    <tr
      onMouseEnter={() => interactive && setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        borderBottom: `1px solid ${C.border}50`,
        background: isHovered ? C.bg3 : (highlight ? `${C.accent}08` : "transparent"),
        cursor: interactive ? "pointer" : "default",
        transition: "all 0.15s cubic-bezier(0.4, 0, 0.2, 1)"
      }}
    >
      {children}
    </tr>
  );
};

const TableCell = ({ children, align = "left", color = C.t1 }) => (
  <td style={{
    color: color,
    padding: "10px 12px",
    textAlign: align,
    fontSize: 11
  }}>
    {children}
  </td>
);

const ProgressBar = ({ v, max, color = C.accent, h = 4 }) => (
  <div style={{ background: C.bg3, borderRadius: C.radius.sm, height: h, overflow: "hidden", border: `1px solid ${C.border}` }}>
    <div style={{ width: `${Math.min((v / max) * 100, 100)}%`, background: color, height: "100%", borderRadius: C.radius.sm, transition: "width 0.5s ease" }} />
  </div>
);

// ── Custom Tooltip ──────────────────────────────────────────
const CustomTooltip = ({ active, payload, label, prefix = "$", suffix = "" }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 12px" }}>
      <p style={{ color: C.t2 }} className="text-[10px] font-mono mb-1">{label}</p>
      {payload.map((p, i) => (
        <p key={i} style={{ color: p.color || C.cyan }} className="text-xs font-mono font-bold">
          {prefix}{typeof p.value === "number" ? p.value.toLocaleString() : p.value}{suffix}
        </p>
      ))}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════
//  UX FEEDBACK COMPONENTS
// ═══════════════════════════════════════════════════════════════════

// ── Loading Spinner ─────────────────────────────────────────
const Spinner = ({ size = 16, color = C.accent }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 16 16"
    style={{ animation: "spin 1s linear infinite" }}
  >
    <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    <circle
      cx="8"
      cy="8"
      r="6"
      fill="none"
      stroke={color}
      strokeWidth="2"
      strokeDasharray="10 20"
      strokeLinecap="round"
    />
  </svg>
);

const LoadingOverlay = ({ text = "Loading..." }) => (
  <div style={{
    position: "absolute",
    inset: 0,
    background: `${C.bg0}80`,
    backdropFilter: "blur(2px)",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: 12,
    zIndex: 50
  }}>
    <Spinner size={24} />
    <span style={{ color: C.t2, fontSize: 11, fontFamily: "monospace" }}>{text}</span>
  </div>
);

// ── Live Status Indicator ───────────────────────────────────
const LiveStatus = ({ isLive = true, label, pulse = true }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
    <span style={{
      width: 8,
      height: 8,
      borderRadius: "50%",
      background: isLive ? C.profit : C.loss,
      boxShadow: isLive && pulse ? `0 0 0 4px ${C.profit}20` : undefined,
      animation: pulse && isLive ? "pulse 2s ease-in-out infinite" : undefined
    }}>
      <style>{`@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }`}</style>
    </span>
    <span style={{ color: isLive ? C.profit : C.loss, fontSize: 10, fontWeight: 600, fontFamily: "monospace", textTransform: "uppercase" }}>
      {label || (isLive ? "LIVE" : "OFFLINE")}
    </span>
  </div>
);

// ── Toast Notification System ───────────────────────────────
const Toast = ({ id, type, message, onClose }) => {
  const icons = {
    trade: <ArrowUpRight size={14} color={C.profit} />,
    error: <XCircle size={14} color={C.loss} />,
    strategy: <Bot size={14} color={C.accent} />,
    info: <Info size={14} color={C.t2} />
  };

  const colors = {
    trade: C.profit,
    error: C.loss,
    strategy: C.accent,
    info: C.t2
  };

  useEffect(() => {
    const timer = setTimeout(() => onClose(id), 4000);
    return () => clearTimeout(timer);
  }, [id, onClose]);

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 10,
      padding: "10px 14px",
      background: C.bg2,
      border: `1px solid ${C.border}`,
      borderLeft: `3px solid ${colors[type] || C.accent}`,
      borderRadius: 6,
      boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
      minWidth: 240,
      maxWidth: 360,
      animation: "slideIn 0.2s ease-out"
    }}>
      <style>{`@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }`}</style>
      {icons[type] || icons.info}
      <span style={{ color: C.t1, fontSize: 12, fontFamily: "monospace", flex: 1 }}>{message}</span>
      <button
        onClick={() => onClose(id)}
        style={{ background: "transparent", border: "none", color: C.t3, cursor: "pointer", padding: 0 }}
      >
        <XCircle size={14} />
      </button>
    </div>
  );
};

const ToastContainer = ({ toasts, onRemove }) => (
  <div style={{
    position: "fixed",
    top: 16,
    right: 16,
    display: "flex",
    flexDirection: "column",
    gap: 8,
    zIndex: 9999,
    pointerEvents: "none"
  }}>
    {toasts.map(t => (
      <div key={t.id} style={{ pointerEvents: "auto" }}>
        <Toast id={t.id} type={t.type} message={t.message} onClose={onRemove} />
      </div>
    ))}
  </div>
);

// ═══════════════════════════════════════════════════════════════════
//  ERROR HANDLING UTILITIES (STEP 11)
// ═══════════════════════════════════════════════════════════════════

/**
 * 🔴 STEP 11: Extract clear error message from backend response
 * Handles various error formats from the API
 * 
 * @param {Error} err - Error object from catch block
 * @param {string} fallback - Fallback message if extraction fails
 * @returns {string} Clear error message to display
 */
const extractErrorMessage = (err, fallback = 'An error occurred') => {
  // Try to get detail from response
  const detail = err?.response?.data?.detail;

  // If detail is an object with reasons array (complex validation error)
  if (typeof detail === 'object' && detail !== null) {
    if (detail.reasons && Array.isArray(detail.reasons)) {
      return detail.reasons.join(', ');
    }
    if (detail.message) {
      return detail.message;
    }
    if (detail.error) {
      return detail.error;
    }
    // Try to stringify if it's a generic object
    try {
      return JSON.stringify(detail);
    } catch {
      // Fall through to other options
    }
  }

  // If detail is a string, use it directly
  if (typeof detail === 'string') {
    return detail;
  }

  // Try other common error properties
  if (err?.response?.data?.message) {
    return err.response.data.message;
  }

  if (err?.response?.data?.error) {
    return err.response.data.error;
  }

  // Use error message or fallback
  return err?.message || fallback;
};

/**
 * 🔴 STEP 11: Get error type for categorization
 */
const getErrorType = (err) => {
  const status = err?.response?.status;
  if (status === 422) return 'validation';
  if (status === 404) return 'missing_data';
  if (status >= 500) return 'backend_failure';
  if (status === 401 || status === 403) return 'auth';
  if (status === 429) return 'rate_limit';
  return 'unknown';
};

// ═══════════════════════════════════════════════════════════════════
//  GLOBAL LOADING CONTEXT
// ═══════════════════════════════════════════════════════════════════

const LoadingContext = React.createContext(null);

const LoadingProvider = ({ children }) => {
  const [loadingStates, setLoadingStates] = useState({});

  const setLoading = useCallback((key, isLoading, message = "") => {
    setLoadingStates(prev => ({
      ...prev,
      [key]: { loading: isLoading, message }
    }));
  }, []);

  const startLoading = useCallback((key, message = "Loading...") => {
    setLoading(key, true, message);
  }, [setLoading]);

  const stopLoading = useCallback((key) => {
    setLoading(key, false, "");
  }, [setLoading]);

  const isLoading = useCallback((key) => {
    return loadingStates[key]?.loading || false;
  }, [loadingStates]);

  const getMessage = useCallback((key) => {
    return loadingStates[key]?.message || "";
  }, [loadingStates]);

  const anyLoading = useMemo(() => {
    return Object.values(loadingStates).some(s => s.loading);
  }, [loadingStates]);

  const value = useMemo(() => ({
    setLoading,
    startLoading,
    stopLoading,
    isLoading,
    getMessage,
    anyLoading,
    states: loadingStates
  }), [setLoading, startLoading, stopLoading, isLoading, getMessage, anyLoading, loadingStates]);

  return (
    <LoadingContext.Provider value={value}>
      {children}
      {anyLoading && (
        <div style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: `${C.bg0}80`,
          backdropFilter: "blur(2px)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 16,
          zIndex: 9998
        }}>
          <Spinner size={32} />
          <div style={{ color: C.t2, fontSize: 12, fontFamily: "monospace" }}>
            {Object.entries(loadingStates)
              .filter(([_, s]) => s.loading)
              .map(([k, s]) => s.message || k)
              .join(" | ")}
          </div>
        </div>
      )}
    </LoadingContext.Provider>
  );
};

const useLoading = (key) => {
  const context = React.useContext(LoadingContext);
  if (!context) {
    throw new Error("useLoading must be used within LoadingProvider");
  }

  if (key) {
    return {
      isLoading: context.isLoading(key),
      message: context.getMessage(key),
      start: (msg) => context.startLoading(key, msg),
      stop: () => context.stopLoading(key)
    };
  }

  return context;
};

// Expose globally for non-React contexts
const setGlobalLoading = (key, loading, message) => {
  window.dispatchEvent(new CustomEvent("global-loading", { detail: { key, loading, message } }));
};

// ═══════════════════════════════════════════════════════════════════
//  SKELETON LOADERS
// ═══════════════════════════════════════════════════════════════════

const SkeletonLine = ({ width = "100%", height = 12, mb = 0 }) => (
  <div style={{
    width,
    height,
    background: `linear-gradient(90deg, ${C.bg3} 25%, ${C.bg4} 50%, ${C.bg3} 75%)`,
    backgroundSize: "200% 100%",
    animation: "shimmer 1.5s infinite",
    borderRadius: C.radius.sm,
    marginBottom: mb
  }}>
    <style>{`@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }`}</style>
  </div>
);

const SkeletonCard = ({ height = 120 }) => (
  <div style={{
    height,
    background: C.bg2,
    border: `1px solid ${C.border}`,
    borderRadius: C.radius.lg,
    padding: C.space.lg,
    display: "flex",
    flexDirection: "column",
    gap: C.space.md
  }}>
    <SkeletonLine width="60%" height={14} />
    <SkeletonLine width="80%" />
    <SkeletonLine width="40%" />
  </div>
);

const SkeletonTable = ({ rows = 5 }) => (
  <div style={{ display: "flex", flexDirection: "column", gap: C.space.sm }}>
    <div style={{ display: "flex", gap: C.space.md, paddingBottom: C.space.sm, borderBottom: `1px solid ${C.border}` }}>
      <SkeletonLine width="20%" height={10} />
      <SkeletonLine width="30%" height={10} />
      <SkeletonLine width="25%" height={10} />
      <SkeletonLine width="25%" height={10} />
    </div>
    {Array.from({ length: rows }).map((_, i) => (
      <div key={i} style={{ display: "flex", gap: C.space.md, padding: `${C.space.sm}px 0` }}>
        <SkeletonLine width="20%" />
        <SkeletonLine width="30%" />
        <SkeletonLine width="25%" />
        <SkeletonLine width="25%" />
      </div>
    ))}
  </div>
);

// ═══════════════════════════════════════════════════════════════════
//  ERROR STATE COMPONENT
// ═══════════════════════════════════════════════════════════════════

const ErrorState = ({ title = "Failed to load data", message = "Something went wrong", onRetry, compact = false }) => (
  <div style={{
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: compact ? C.space.lg : C.space.xxl,
    gap: C.space.md,
    textAlign: "center"
  }}>
    <AlertTriangle size={compact ? 24 : 32} color={C.warning} />
    <div>
      <p style={{ color: C.t1, fontSize: compact ? 12 : 14, fontWeight: 600, marginBottom: 4 }}>{title}</p>
      {message && <p style={{ color: C.t3, fontSize: compact ? 10 : 11 }}>{message}</p>}
    </div>
    {onRetry && (
      <button
        onClick={onRetry}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "6px 12px",
          background: C.bg3,
          border: `1px solid ${C.border}`,
          borderRadius: C.radius.md,
          color: C.t2,
          fontSize: 11,
          fontFamily: "monospace",
          cursor: "pointer",
          transition: "all 0.15s ease"
        }}
        onMouseEnter={(e) => { e.target.style.borderColor = C.borderLight; e.target.style.color = C.t1; }}
        onMouseLeave={(e) => { e.target.style.borderColor = C.border; e.target.style.color = C.t2; }}
      >
        <RefreshCw size={12} />
        Retry
      </button>
    )}
  </div>
);

// ═══════════════════════════════════════════════════════════════════
//  REAL-TIME DATA POLLING HOOK
// ═══════════════════════════════════════════════════════════════════

const usePolling = (fetchFn, interval = 5000, options = {}) => {
  const { onError, onSuccess, enabled = true } = options;
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  const fetch = useCallback(async () => {
    try {
      setLoading(prev => !prev || data === null);
      const result = await fetchFn();
      setData(result);
      setError(null);
      setLastUpdated(new Date());
      onSuccess?.(result);
    } catch (err) {
      setError(err.message || "Failed to fetch");
      onError?.(err);
    } finally {
      setLoading(false);
    }
  }, [fetchFn, onSuccess, onError, data]);

  useEffect(() => {
    if (!enabled) return;
    fetch();
    const id = setInterval(fetch, interval);
    return () => clearInterval(id);
  }, [fetch, interval, enabled]);

  return { data, loading, error, refetch: fetch, lastUpdated };
};

// ═══════════════════════════════════════════════════════════════════
//  LIVE LOG STREAM
// ═══════════════════════════════════════════════════════════════════

const LiveLogStream = ({ logs = [], maxHeight = 200 }) => {
  const scrollRef = useRef(null);
  const [autoScroll, setAutoScroll] = useState(true);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const icons = {
    info: <Info size={10} color={C.t3} />,
    trade: <ArrowUpRight size={10} color={C.profit} />,
    signal: <Zap size={10} color={C.warning} />,
    error: <XCircle size={10} color={C.loss} />,
    system: <Activity size={10} color={C.accent} />
  };

  return (
    <div style={{
      background: C.bg2,
      border: `1px solid ${C.border}`,
      borderRadius: C.radius.lg,
      overflow: "hidden"
    }}>
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "8px 12px",
        borderBottom: `1px solid ${C.border}`,
        background: C.bg3
      }}>
        <span style={{ color: C.t2, fontSize: 10, fontWeight: 600, textTransform: "uppercase" }}>Live Logs</span>
        <button
          onClick={() => setAutoScroll(!autoScroll)}
          style={{
            fontSize: 9,
            color: autoScroll ? C.accent : C.t3,
            background: "transparent",
            border: "none",
            cursor: "pointer"
          }}
        >
          {autoScroll ? "Auto-scroll ON" : "Auto-scroll OFF"}
        </button>
      </div>
      <div
        ref={scrollRef}
        style={{
          maxHeight,
          overflowY: "auto",
          padding: C.space.md,
          fontFamily: "monospace",
          fontSize: 10
        }}
      >
        {logs.length === 0 ? (
          <p style={{ color: C.t4, textAlign: "center", padding: C.space.lg }}>No logs yet...</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {logs.map((log, i) => (
              <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                <span style={{ color: C.t4, fontSize: 9, minWidth: 50 }}>
                  {log.time || new Date().toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                </span>
                {icons[log.type] || icons.info}
                <span style={{ color: log.type === "error" ? C.loss : log.type === "trade" ? C.profit : C.t2 }}>
                  {log.message}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════
//  ENHANCED LIVE STATUS (with loading state)
// ═══════════════════════════════════════════════════════════════════

const LiveStatusV2 = ({ status = "stopped", label }) => {
  const configs = {
    running: { color: C.profit, bgPulse: true, blink: false, label: label || "LIVE" },
    stopped: { color: C.loss, bgPulse: false, blink: false, label: label || "STOPPED" },
    loading: { color: C.warning, bgPulse: false, blink: true, label: label || "LOADING" },
    paused: { color: C.warning, bgPulse: false, blink: false, label: label || "PAUSED" },
    connecting: { color: C.accent, bgPulse: false, blink: true, label: label || "CONNECTING" }
  };

  const config = configs[status] || configs.stopped;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: config.color,
        boxShadow: config.bgPulse ? `0 0 0 4px ${config.color}20` : undefined,
        animation: config.bgPulse ? "pulse 2s ease-in-out infinite" : config.blink ? "blink 1s ease-in-out infinite" : undefined
      }}>
        <style>{`
          @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
          @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
        `}</style>
      </span>
      <span style={{ color: config.color, fontSize: 10, fontWeight: 600, fontFamily: "monospace", textTransform: "uppercase" }}>
        {config.label}
      </span>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════
//  BUTTON WITH LOADING STATE
// ═══════════════════════════════════════════════════════════════════

const AsyncBtn = ({ children, onClick, loading = false, loadingText, disabled, v = "primary", sz = "md", Icon }) => {
  const isDisabled = disabled || loading;

  return (
    <Btn v={v} sz={sz} Icon={loading ? null : Icon} onClick={onClick} disabled={isDisabled}>
      {loading ? (
        <>
          <Spinner size={sz === "lg" ? 14 : sz === "sm" ? 10 : 12} />
          <span>{loadingText || children}</span>
        </>
      ) : (
        children
      )}
    </Btn>
  );
};

// ═══════════════════════════════════════════════════════════════════
//  PREMIUM UI COMPONENTS
// ═══════════════════════════════════════════════════════════════════

// ── Empty State with Animation ──────────────────────────────
const EmptyState = ({ icon: Icon, title, subtitle, action, actionLabel, hint, size = "md" }) => {
  const sizes = {
    sm: { icon: 24, title: 12, subtitle: 10, padding: 20 },
    md: { icon: 40, title: 14, subtitle: 12, padding: 40 },
    lg: { icon: 64, title: 18, subtitle: 14, padding: 60 }
  };
  const s = sizes[size];

  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      padding: s.padding,
      gap: 12,
      textAlign: "center"
    }}>
      {Icon && (
        <div style={{
          animation: "float 3s ease-in-out infinite",
          opacity: 0.5
        }}>
          <style>{`
            @keyframes float {
              0%, 100% { transform: translateY(0px); }
              50% { transform: translateY(-8px); }
            }
          `}</style>
          <Icon size={s.icon} color={C.t3} strokeWidth={1} />
        </div>
      )}
      <div>
        <p style={{ color: C.t2, fontSize: s.title, fontWeight: 600, marginBottom: 4 }}>{title}</p>
        {subtitle && <p style={{ color: C.t3, fontSize: s.subtitle }}>{subtitle}</p>}
      </div>
      {hint && (
        <div style={{
          background: C.bg3,
          border: `1px dashed ${C.border}`,
          borderRadius: C.radius.md,
          padding: "8px 12px",
          marginTop: 8
        }}>
          <p style={{ color: C.t4, fontSize: 10, fontStyle: "italic" }}>💡 {hint}</p>
        </div>
      )}
      {action && actionLabel && (
        <Btn v="outline" sz="sm" onClick={action}>{actionLabel}</Btn>
      )}
    </div>
  );
};

// ── PnL Glow Badge ───────────────────────────────────────────
const PnLBadge = ({ value, prefix = "$", size = "md", animated = true }) => {
  const isProfit = value >= 0;
  const color = isProfit ? C.profit : C.loss;
  const glow = isProfit ? C.glow.profit : C.glow.loss;
  const sizes = { sm: 12, md: 16, lg: 24, xl: 32 };
  const s = sizes[size];

  return (
    <span style={{
      color,
      fontSize: s,
      fontWeight: 700,
      fontFamily: "monospace",
      textShadow: glow,
      animation: animated && value !== 0 ? "pulseGlow 2s ease-in-out infinite" : undefined,
      display: "inline-flex",
      alignItems: "center",
      gap: 4
    }}>
      <style>{`
        @keyframes pulseGlow {
          0%, 100% { text-shadow: ${glow}; }
          50% { text-shadow: none; }
        }
      `}</style>
      {isProfit ? "+" : ""}{prefix}{Math.abs(value).toLocaleString()}
    </span>
  );
};

// ── Animated Number ─────────────────────────────────────────
const AnimatedNumber = ({ value, prefix = "", suffix = "", duration = 500 }) => {
  const [display, setDisplay] = useState(value);
  const prevValue = useRef(value);

  useEffect(() => {
    const start = prevValue.current;
    const end = value;
    const diff = end - start;
    const startTime = performance.now();

    const animate = (currentTime) => {
      const elapsed = currentTime - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const easeOut = 1 - Math.pow(1 - progress, 3);
      const current = start + diff * easeOut;

      setDisplay(current);

      if (progress < 1) {
        requestAnimationFrame(animate);
      } else {
        prevValue.current = value;
      }
    };

    requestAnimationFrame(animate);
  }, [value, duration]);

  return (
    <span>{prefix}{display.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}{suffix}</span>
  );
};

// ── Risk Meter ───────────────────────────────────────────────
const RiskMeter = ({ value, max, label, warningAt = 0.7, dangerAt = 0.9 }) => {
  const percentage = Math.min((value / max) * 100, 100);
  const ratio = value / max;

  let color = C.profit;
  let status = "SAFE";
  if (ratio >= dangerAt) {
    color = C.loss;
    status = "CRITICAL";
  } else if (ratio >= warningAt) {
    color = C.warning;
    status = "WARNING";
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ color: C.t2, fontSize: 10, fontWeight: 600 }}>{label}</span>
        <span style={{ color, fontSize: 10, fontWeight: 700 }}>{status}</span>
      </div>
      <div style={{
        height: 6,
        background: C.bg3,
        borderRadius: 3,
        overflow: "hidden",
        position: "relative"
      }}>
        {/* Warning zone marker */}
        <div style={{
          position: "absolute",
          left: `${warningAt * 100}%`,
          top: 0,
          bottom: 0,
          width: 1,
          background: C.warning,
          opacity: 0.5
        }} />
        {/* Danger zone marker */}
        <div style={{
          position: "absolute",
          left: `${dangerAt * 100}%`,
          top: 0,
          bottom: 0,
          width: 1,
          background: C.loss,
          opacity: 0.5
        }} />
        {/* Fill */}
        <div style={{
          width: `${percentage}%`,
          height: "100%",
          background: color,
          borderRadius: 3,
          transition: "width 0.5s ease, background 0.3s ease",
          boxShadow: ratio >= warningAt ? C.glow[color === C.loss ? "loss" : color === C.warning ? "warning" : "profit"] : undefined
        }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: C.t3 }}>
        <span>{value.toLocaleString()}</span>
        <span>{max.toLocaleString()}</span>
      </div>
    </div>
  );
};

// ── Premium Card with Hover Lift ─────────────────────────────
const PremiumCard = ({ children, title, icon: Icon, action, glowOnHover = false, glowColor = "accent", borderAccent = false, style = {} }) => {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        background: C.bg2,
        border: `1px solid ${borderAccent && isHovered ? C[glowColor] || C.accent : C.border}`,
        borderRadius: C.radius.lg,
        overflow: "hidden",
        transition: "all 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
        transform: isHovered ? "translateY(-2px)" : "translateY(0)",
        boxShadow: isHovered && glowOnHover ? `${C.shadowMd}, ${C.glow[glowColor] || C.glow.accent}` : C.shadow,
        ...style
      }}
    >
      {title && (
        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 16px",
          borderBottom: `1px solid ${C.border}`,
          background: C.gradient.card
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {Icon && <Icon size={14} color={C.t2} />}
            <span style={{ color: C.t1, fontSize: 12, fontWeight: 600 }}>{title}</span>
          </div>
          {action}
        </div>
      )}
      <div style={{ padding: title ? "16px" : 0 }}>
        {children}
      </div>
    </div>
  );
};

// ── Status Dot with Pulse ──────────────────────────────────
const StatusDot = ({ status = "idle", size = 8, pulse = true }) => {
  const colors = {
    idle: C.t3,
    live: C.profit,
    running: C.profit,
    stopped: C.t3,
    backtesting: C.accent,
    paused: C.warning,
    warning: C.warning,
    error: C.loss,
    active: C.accent
  };

  const color = colors[status] || colors.idle;
  const shouldPulse = pulse && (status === "live" || status === "active" || status === "running");

  return (
    <span style={{
      width: size,
      height: size,
      borderRadius: "50%",
      background: color,
      display: "inline-block",
      boxShadow: shouldPulse ? `0 0 0 ${size / 2}px ${color}40` : undefined,
      animation: shouldPulse ? "statusPulse 2s ease-in-out infinite" : undefined
    }}>
      <style>{`
        @keyframes statusPulse {
          0%, 100% { box-shadow: 0 0 0 0px ${color}60; }
          50% { box-shadow: 0 0 0 ${size}px ${color}00; }
        }
      `}</style>
    </span>
  );
};

// ── Mini Sparkline ───────────────────────────────────────────
const MiniSparkline = ({ data = [], width = 80, height = 24, color = C.accent, fill = true }) => {
  if (!data.length) return <div style={{ width, height }} />;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((v - min) / range) * height;
    return `${x},${y}`;
  }).join(" ");

  return (
    <svg width={width} height={height} style={{ overflow: "visible" }}>
      <defs>
        <linearGradient id={`sparkFill-${color.replace("#", "")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.3} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      {fill && (
        <polygon
          points={`0,${height} ${points} ${width},${height}`}
          fill={`url(#sparkFill-${color.replace("#", "")})`}
        />
      )}
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
};

// ═══════════════════════════════════════════════════════════════════
//  SIDEBAR
// ═══════════════════════════════════════════════════════════════════
const NAV = [
  { id: "dashboard", lbl: "Dashboard", Icon: LayoutDashboard, g: "core" },
  { id: "bot-monitor", lbl: "Bot Monitor", Icon: Bot, g: "core" },
  { id: "signal-trace", lbl: "Signal Trace", Icon: Activity, g: "core" },
  // Infrastructure Ops removed from client navigation - admin only
  // Terminal removed - not applicable for algo-only platform
  // Live Trading disabled - {id:"trading", lbl:"Live Trading", Icon:TrendingUp, g:"core"},
  { id: "builder", lbl: "Strategy Builder", Icon: Cpu, g: "core" },
  { id: "strategies", lbl: "Strategies", Icon: Layers, g: "core" },
  // portfolio removed - manual trading feature not applicable
  // history removed - manual trading feature not applicable
  { id: "exchange", lbl: "Exchanges", Icon: Link2, g: "vault" },
  { id: "risk", lbl: "Risk Settings", Icon: Shield, g: "vault" },
  { id: "billing", lbl: "Billing", Icon: CreditCard, g: "vault" },
  { id: "leaderboard", lbl: "Leaderboard", Icon: Award, g: "platform" },
  { id: "referral", lbl: "Referral", Icon: Gift, g: "platform" },
  { id: "docs", lbl: "Docs", Icon: BookOpen, g: "platform" },
  { id: "support", lbl: "Support", Icon: HelpCircle, g: "platform" },
  { id: "notifications", lbl: "Notifications", Icon: Bell, g: "platform" },
];
const G_LABELS = { core: "Command Center", vault: "Vault", platform: "Platform" };

function Sidebar({ page, go }) {
  const groups = ["core", "vault", "platform"];
  return (
    <aside style={{ background: C.bg1, borderRight: `1px solid ${C.border}`, width: 210, display: "flex", flexDirection: "column", flexShrink: 0, minHeight: "100vh" }}>
      {/* Logo */}
      <div style={{ borderBottom: `1px solid ${C.border}`, padding: "14px 16px" }} className="flex items-center gap-2.5">
        <div style={{ background: "linear-gradient(135deg,#00d4ff,#0055ff)", borderRadius: 8, padding: "6px 7px", flexShrink: 0 }}>
          <Zap size={15} color="#000" strokeWidth={2.5} />
        </div>
        <div>
          <div style={{ color: C.t1 }} className="text-base font-black tracking-tight leading-none">VYOM<span style={{ color: C.cyan }}>QUANT</span></div>
          <div style={{ color: C.t3 }} className="text-[8px] font-mono tracking-widest mt-0.5">QUANT INFRASTRUCTURE</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 py-3" style={{ scrollbarWidth: "none" }}>
        {groups.filter(g => !g.includes("admin")).map(g => (
          <div key={g} className="mb-3">
            <div style={{ color: C.t3 }} className="text-[8px] font-mono font-black tracking-widest uppercase px-2 mb-1">{G_LABELS[g]}</div>
            {NAV.filter(n => n.g === g).map(n => {
              const active = page === n.id;
              return (
                <button key={n.id} onClick={() => {
                  if (n.id === "docs") {
                    window.open("https://docs.algo22.io", "_blank");
                  } else {
                    go(n.id);
                  }
                }}
                  style={{
                    background: active ? "rgba(0,212,255,0.07)" : "transparent",
                    color: active ? C.cyan : C.t2,
                    borderLeft: `2px solid ${active ? C.cyan : "transparent"}`,
                    width: "100%", display: "flex", alignItems: "center", gap: 8,
                    padding: "6px 8px", borderRadius: "0 8px 8px 0", fontSize: 11,
                    fontFamily: "monospace", fontWeight: 600, transition: "all 0.15s",
                    cursor: "pointer", marginBottom: 1,
                  }}
                  className="hover:text-cyan-400 hover:bg-cyan-500/5">
                  <n.Icon size={12} />
                  <span>{n.lbl}</span>
                  {n.id === "docs" && <ExternalLink size={10} style={{ marginLeft: 4, color: C.t3 }} />}
                  {n.id === "notifs" && <span style={{ marginLeft: "auto", background: C.red, color: "#fff", borderRadius: 99, fontSize: 8, width: 14, height: 14, display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 900 }}>3</span>}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      {/* User */}
      <div style={{ borderTop: `1px solid ${C.border}`, padding: 10 }}>
        <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10 }} className="p-2 flex items-center gap-2">
          <div style={{ background: C.bg4, border: `1px solid ${C.border}`, borderRadius: "50%", width: 26, height: 26, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, fontWeight: 900, color: C.accent, flexShrink: 0 }}>N</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ color: C.t1 }} className="text-xs font-bold truncate">Neo_Quant</div>
            <div style={{ color: C.t3 }} className="text-[8px] font-mono">PRO TIER</div>
          </div>
          <LogOut size={11} style={{ color: C.t3, flexShrink: 0, cursor: "pointer" }} className="hover:text-red-400" onClick={() => { sessionStorage.removeItem("token"); go("landing"); }} />
        </div>
      </div>
    </aside>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  TOPBAR (CLEAN)
// ═══════════════════════════════════════════════════════════════════
function TopBar({ go }) {
  const [t, setT] = useState(new Date());
  useEffect(() => { const id = setInterval(() => setT(new Date()), 1000); return () => clearInterval(id); }, []);

  return (
    <div style={{ background: C.bg1, borderBottom: `1px solid ${C.border}`, height: 44, display: "flex", alignItems: "center", justifyContent: "flex-end", padding: "0 16px", gap: 16, flexShrink: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <LiveStatusV2 status="running" />
        <span style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }}>{t.toUTCString().slice(17, 25)} UTC</span>
        <button
          onClick={() => go("notifs")}
          onMouseEnter={(e) => e.target.style.color = C.accent}
          onMouseLeave={(e) => e.target.style.color = C.t2}
          style={{ position: "relative", color: C.t2, background: "transparent", border: "none", cursor: "pointer", transition: "color 0.15s ease" }}
        >
          <Bell size={14} />
          <span style={{ position: "absolute", top: -4, right: -4, background: C.loss, borderRadius: "50%", width: 12, height: 12, fontSize: 8, display: "flex", alignItems: "center", justifyContent: "center", color: "#fff", fontWeight: 900 }}>3</span>
        </button>
        <div style={{ width: 1, height: 16, background: C.border, margin: "0 4px" }} />
        <button
          onClick={() => go("profile")}
          onMouseEnter={(e) => e.target.style.color = C.t1}
          onMouseLeave={(e) => e.target.style.color = C.t2}
          style={{ display: "flex", alignItems: "center", gap: 8, background: "transparent", border: "none", color: C.t2, cursor: "pointer", transition: "color 0.15s ease" }}
        >
          <div style={{ background: C.bg4, border: `1px solid ${C.border}`, borderRadius: "50%", width: 24, height: 24, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, fontWeight: 900, color: C.accent }}>N</div>
          <span style={{ fontSize: 11, fontFamily: "monospace", fontWeight: 600 }}>Profile</span>
        </button>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  PAGE: LANDING
// ═══════════════════════════════════════════════════════════════════
const LegalPage = ({ onBack, go }) => {
  const [activeTab, setActiveTab] = useState("tos"); // tos, privacy, risk, refund

  // Tab content with gorgeous dark glassmorphic panels
  const tabs = {
    tos: {
      title: "Terms of Service",
      content: (
        <div style={{ lineHeight: "1.6", color: C.t1 }}>
          <h2 style={{ color: C.accent, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>1. Beta Service & Paper Trading Only</h2>
          <p style={{ marginBottom: "12px" }}>The Beta Service is provided solely for testing, evaluation, and paper trading purposes.</p>
          <ul style={{ paddingLeft: "20px", marginBottom: "16px", listStyleType: "square", color: C.t2 }}>
            <li><strong>NO REAL MONETARY TRANSACTIONS OR DEPLOYMENTS</strong>: You acknowledge that the Beta Service uses simulated balances and does not perform live trades on real exchange accounts.</li>
            <li><strong>AS-IS BASIS</strong>: Provided on an "AS IS" and "AS AVAILABLE" basis.</li>
          </ul>
          <h2 style={{ color: C.accent, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>2. Eligibility & Accounts</h2>
          <p style={{ marginBottom: "16px" }}>You are responsible for keeping your credentials (including API keys, passwords, and 2FA settings) secure.</p>
          <h2 style={{ color: C.accent, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>3. Subscription Tiers & Billing</h2>
          <p style={{ marginBottom: "16px" }}>Subscriptions are billed in advance. All transactions are processed via third-party providers (Stripe, Razorpay).</p>
          <h2 style={{ color: C.accent, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>4. Prohibited Uses</h2>
          <p style={{ marginBottom: "16px" }}>You agree not to reverse engineer, decompile, or attempt to extract source code.</p>
        </div>
      )
    },
    privacy: {
      title: "Privacy Policy",
      content: (
        <div style={{ lineHeight: "1.6", color: C.t1 }}>
          <h2 style={{ color: C.accent, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>1. Information We Collect</h2>
          <p style={{ marginBottom: "12px" }}>We collect account credentials via Supabase Auth, encrypted exchange API keys, usage telemetry, and billing tokens.</p>
          <h2 style={{ color: C.accent, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>2. How We Use Information</h2>
          <p style={{ marginBottom: "12px" }}>To authenticate users, execute backtests/model training, verify webhooks, and optimize platform performance.</p>
          <h2 style={{ color: C.accent, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>3. Data Storage & Security</h2>
          <p style={{ marginBottom: "12px" }}>Authentication data is stored securely via Supabase Auth. API keys and secrets are encrypted in transit and at rest.</p>
        </div>
      )
    },
    risk: {
      title: "Risk Disclosure",
      content: (
        <div style={{ lineHeight: "1.6", color: C.t1 }}>
          <h2 style={{ color: C.loss, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>⚠️ High-Risk Investment Warning</h2>
          <p style={{ marginBottom: "12px" }}>Trading financial instruments involves substantial risk of loss and is not suitable for every investor.</p>
          <h2 style={{ color: C.accent, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>1. Algorithmic & Quantitative Trading Risk</h2>
          <p style={{ marginBottom: "12px" }}>Automated strategies execute based on predefined rules. Bugs, logic errors, or connection drops can lead to unexpected and costly executions.</p>
          <h2 style={{ color: C.accent, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>2. Simulated / Paper Trading Limitations</h2>
          <p style={{ marginBottom: "12px" }}>Paper trading uses simulated balances and idealized execution. It does not account for real-world liquidity, market impact, slippage, or latency.</p>
        </div>
      )
    },
    refund: {
      title: "Refund Policy",
      content: (
        <div style={{ lineHeight: "1.6", color: C.t1 }}>
          <h2 style={{ color: C.accent, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>1. Digital Subscription Tiers</h2>
          <p style={{ marginBottom: "12px" }}>All subscription payments (Pro, Enterprise, and ML Add-ons) are digital software entitlements activated immediately. All purchases are final and non-refundable.</p>
          <h2 style={{ color: C.accent, fontSize: "14px", marginBottom: "12px", textTransform: "uppercase" }}>2. Cancellation and Renewal</h2>
          <p style={{ marginBottom: "12px" }}>You may cancel your subscription at any time. Upon cancellation, you retain access until the end of your billing period.</p>
        </div>
      )
    }
  };

  return (
    <div style={{
      background: "rgba(14, 19, 25, 0.75)",
      backdropFilter: "blur(20px)",
      border: `1px solid ${C.border}`,
      borderRadius: C.radius.xl,
      padding: "24px",
      maxWidth: "800px",
      width: "100%",
      margin: "40px auto",
      boxShadow: "0 20px 40px rgba(0,0,0,0.5)",
      fontFamily: "monospace"
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "20px" }}>
        <h1 style={{ color: C.t1, fontSize: "16px", fontWeight: "800", display: "flex", alignItems: "center", gap: 10 }}>
          <Shield size={16} color={C.accent} /> LEGAL & RISK CENTER
        </h1>
        {onBack && (
          <button onClick={onBack} style={{
            background: C.bg3,
            border: `1px solid ${C.border}`,
            color: C.t2,
            padding: "6px 12px",
            borderRadius: C.radius.md,
            cursor: "pointer",
            fontSize: "10px",
            transition: "all 0.2s"
          }}
            onMouseEnter={(e) => { e.target.style.background = C.bg4; e.target.style.color = C.t1; }}
            onMouseLeave={(e) => { e.target.style.background = C.bg3; e.target.style.color = C.t2; }}
          >
            ← BACK
          </button>
        )}
      </div>

      {/* Tabs list */}
      <div style={{ display: "flex", borderBottom: `1px solid ${C.border}`, gap: 8, marginBottom: "20px" }}>
        {Object.entries(tabs).map(([key, tab]) => {
          const isActive = activeTab === key;
          return (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              style={{
                background: isActive ? `${C.accent}15` : "transparent",
                border: "none",
                borderBottom: isActive ? `2px solid ${C.accent}` : "2px solid transparent",
                color: isActive ? C.t1 : C.t3,
                padding: "10px 16px",
                fontSize: "10px",
                fontWeight: "700",
                cursor: "pointer",
                transition: "all 0.2s",
                fontFamily: "monospace"
              }}
            >
              {tab.title.toUpperCase()}
            </button>
          );
        })}
      </div>

      {/* Tab content panel */}
      <div style={{
        background: "rgba(0, 0, 0, 0.2)",
        border: `1px solid ${C.border}80`,
        borderRadius: C.radius.lg,
        padding: "20px",
        minHeight: "280px"
      }}>
        {tabs[activeTab].content}
      </div>
    </div>
  );
};

function LandingFeatureCard({ f, i }) {
  const [isHovered, setIsHovered] = useState(false);
  const colors = [C.accent, C.profit, C.gold];
  const cardColor = colors[i % colors.length];

  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        background: C.bg2,
        border: `1px solid ${isHovered ? cardColor : C.border}`,
        borderRadius: 16,
        padding: 26,
        backdropFilter: "blur(8px)",
        boxShadow: isHovered
          ? `inset 0 1px 0 rgba(255,255,255,0.03), 0 24px 48px rgba(0,0,0,0.35), ${C.glow[cardColor === C.profit ? "profit" : cardColor === C.gold ? "gold" : "accent"]}`
          : `inset 0 1px 0 rgba(255,255,255,0.03), 0 24px 48px rgba(0,0,0,0.35)`,
        transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
        transform: isHovered ? "translateY(-4px)" : "translateY(0)",
        cursor: "default"
      }}
    >
      <div
        style={{
          background: isHovered ? `${cardColor}20` : `${C.accent}14`,
          border: `1px solid ${isHovered ? cardColor : C.accent}40`,
          borderRadius: 10,
          padding: 9,
          display: "inline-flex",
          marginBottom: 16,
          transition: "all 0.3s ease",
          boxShadow: isHovered ? `0 0 15px ${cardColor}30` : "none"
        }}
      >
        <f.I size={19} style={{ color: isHovered ? cardColor : C.accent, transition: "color 0.3s ease" }} />
      </div>
      <div style={{ color: C.t1, fontSize: 18, fontWeight: 900, marginBottom: 8 }}>{f.t}</div>
      <div style={{ color: C.t2, fontSize: 12, lineHeight: 1.75 }}>{f.d}</div>
    </div>
  );
}

function LandingPricingCard({ plan, go }) {
  const [isHovered, setIsHovered] = useState(false);
  const isElite = plan.name === "Elite";
  const isPro = plan.name === "Pro";

  const borderColor = isElite ? C.gold : (isPro ? C.accent : C.border);
  const glowShadow = isElite ? C.glow.gold : (isPro ? C.glow.accent : "none");
  const badgeColor = isElite ? C.gold : (isPro ? C.accent : C.t3);

  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        background: C.bg2,
        border: `1px solid ${isHovered ? borderColor : C.border}`,
        borderRadius: 20,
        padding: 32,
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        position: "relative",
        boxShadow: isHovered
          ? `0 30px 60px rgba(0,0,0,0.4), ${glowShadow}`
          : "0 20px 40px rgba(0,0,0,0.2)",
        transform: isHovered ? "translateY(-6px)" : "translateY(0)",
        transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)"
      }}
    >
      {(isPro || isElite) && (
        <span
          style={{
            position: "absolute",
            top: -12,
            right: 24,
            background: badgeColor,
            color: "#000",
            fontSize: 9,
            fontWeight: 900,
            padding: "4px 12px",
            borderRadius: 12,
            fontFamily: "monospace",
            letterSpacing: "0.1em",
            textTransform: "uppercase"
          }}
        >
          {isElite ? "Machine Learning" : "Popular"}
        </span>
      )}
      <div>
        <div style={{ color: C.t1, fontSize: 22, fontWeight: 900, marginBottom: 4 }}>{plan.name}</div>
        <div style={{ color: C.t3, fontSize: 11, fontFamily: "monospace", marginBottom: 20 }}>{plan.desc}</div>
        <div style={{ display: "flex", alignItems: "baseline", marginBottom: 24 }}>
          <span style={{ color: C.t1, fontSize: 36, fontWeight: 900 }}>{plan.price}</span>
          <span style={{ color: C.t3, fontSize: 12, fontFamily: "monospace", marginLeft: 4 }}>/ month</span>
        </div>
        <ul style={{ display: "flex", flexDirection: "column", gap: 12, padding: 0, margin: "0 0 32px 0", listStyle: "none" }}>
          {plan.features.map(f => (
            <li key={f} style={{ display: "flex", alignItems: "center", gap: 8, color: C.t2, fontSize: 12 }}>
              <Check size={12} style={{ color: C.profit }} />
              <span style={{ fontFamily: "monospace" }}>{f}</span>
            </li>
          ))}
        </ul>
      </div>
      <button
        onClick={() => go("auth-signup")}
        style={{
          background: isPro ? C.accent : (isElite ? C.gold : "transparent"),
          border: `1px solid ${isPro ? C.accent : (isElite ? C.gold : C.border)}`,
          color: (isPro || isElite) ? "#000" : C.t1,
          width: "100%",
          padding: "12px 0",
          borderRadius: 10,
          fontWeight: 900,
          fontSize: 12,
          fontFamily: "monospace",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          cursor: "pointer",
          transition: "all 0.2s"
        }}
      >
        {plan.cta}
      </button>
    </div>
  );
}

function LandingFAQItem({ q, a }) {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <div style={{ borderBottom: `1px solid ${C.border}`, padding: "16px 0" }}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        style={{
          width: "100%",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          background: "transparent",
          border: "none",
          color: C.t1,
          fontSize: 15,
          fontWeight: 700,
          cursor: "pointer",
          padding: "8px 0",
          textAlign: "left",
          outline: "none"
        }}
      >
        <span>{q}</span>
        {isOpen ? <ChevronUp size={16} style={{ color: C.accent }} /> : <ChevronDown size={16} style={{ color: C.t3 }} />}
      </button>
      {isOpen && (
        <div style={{ color: C.t2, fontSize: 13, lineHeight: 1.6, padding: "8px 0 12px", fontFamily: "monospace" }}>
          {a}
        </div>
      )}
    </div>
  );
}

function LandingTestimonialCard({ t }) {
  return (
    <div
      style={{
        background: `${C.bg2}aa`,
        border: `1px solid ${C.border}`,
        borderRadius: 16,
        padding: 24,
        boxShadow: "0 10px 30px rgba(0,0,0,0.1)",
        backdropFilter: "blur(4px)"
      }}
    >
      <div style={{ display: "flex", gap: 2, marginBottom: 14 }}>
        {Array.from({ length: 5 }).map((_, i) => (
          <Star key={i} size={11} fill={C.gold} color={C.gold} />
        ))}
      </div>
      <p style={{ color: C.t2, fontSize: 13, lineHeight: 1.6, fontStyle: "italic", marginBottom: 16 }}>
        "{t.quote}"
      </p>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div
          style={{
            background: `${C.accent}22`,
            border: `1px solid ${C.accent}40`,
            borderRadius: "50%",
            width: 30,
            height: 30,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: C.accent,
            fontSize: 10,
            fontWeight: 900,
            fontFamily: "monospace"
          }}
        >
          {t.author.charAt(0)}
        </div>
        <div>
          <div style={{ color: C.t1, fontSize: 12, fontWeight: 900 }}>{t.author}</div>
          <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>{t.role}</div>
        </div>
      </div>
    </div>
  );
}

function Landing({ go }) {
  const [showDemoModal, setShowDemoModal] = useState(false);

  const features = [
    {
      I: GitBranch,
      t: "Visual Strategy Builder",
      d: "Map indicators and entry/exit conditions visually. Custom logic executes seamlessly without coding.",
    },
    {
      I: Activity,
      t: "Backtesting Engine",
      d: "Simulate strategy performance across years of high-fidelity historical market data in seconds.",
    },
    {
      I: BarChart2,
      t: "Monte Carlo Simulation",
      d: "Stress-test strategy risk parameters under hundreds of randomized market scenarios to verify durability.",
    },
    {
      I: TrendingUp,
      t: "Walk Forward Analysis",
      d: "Prevent strategy overfitting with walking out-of-sample testing windows that confirm real-world utility.",
    },
    {
      I: Bot,
      t: "Paper Trading Mode",
      d: "Forward-test your strategies in live markets with simulated balances using exact exchange latency.",
    },
    {
      I: Shield,
      t: "Institutional Risk Controls",
      d: "Enforce strict margin safety limits and automatic global liquidation thresholds at the account level.",
    },
  ];

  const pricingPlans = [
    {
      name: "Free",
      desc: "For exploring the builder and basic backtesting",
      price: "$0",
      features: [
        "1 Deployed Paper Bot",
        "3 Backtests / month",
        "Basic indicator access",
        "No live execution"
      ],
      cta: "Start Free"
    },
    {
      name: "Pro",
      desc: "For active automated traders forward-testing",
      price: "$12",
      features: [
        "5 Deployed Paper Bots",
        "Unlimited Backtesting",
        "Algorithm Indicators access",
        "Advanced performance analytics"
      ],
      cta: "Upgrade to Pro"
    },
    {
      name: "Elite",
      desc: "For professional quants requiring machine learning",
      price: "$24",
      features: [
        "Infinite Deployed Paper Bots",
        "3 ML/DL Model Training Slots",
        "Priority strategy execution queue",
        "24/7 dedicated support SLA"
      ],
      cta: "Go Elite"
    }
  ];

  const testimonials = [
    {
      quote: "Aerora made it possible for me to automate my trading strategies. The visual builder is incredibly intuitive.",
      author: "Sarah K.",
      role: "Quantitative Trader"
    },
    {
      quote: "The Monte Carlo simulation features are institutional grade. Essential for verifying strategy durability before going live.",
      author: "David L.",
      role: "Asset Manager"
    },
    {
      quote: "Sub-millisecond execution and reliable paper trading. My forward testing matches live results perfectly.",
      author: "Michael R.",
      role: "Crypto Scalper"
    }
  ];

  const faqs = [
    {
      q: "Do I need coding experience to use VyomQuant?",
      a: "No coding required. Our visual builder allows you to map logic and indicators using drag-and-drop conditions."
    },
    {
      q: "What is the difference between Paper and Live trading?",
      a: "Paper trading forward-tests strategies with real-time live data and simulated cash. Live trading connects to your exchange API keys to trade real assets (Live mode requires Pro or Elite plan)."
    },
    {
      q: "Can I cancel or change my subscription tier at any time?",
      a: "Yes, you can upgrade, downgrade, or cancel your subscription at any time directly from your billing dashboard."
    },
    {
      q: "Is my API key credentials information secure?",
      a: "Yes. Exchange API keys are encrypted at-rest using AES-256 GCM in our Security Vault. Secrets never leave our secure backend execution path."
    }
  ];

  return (
    <div style={{ background: C.bg, minHeight: "100vh", overflowY: "auto" }}>
      {/* Hero Section */}
      <section
        className="py-28 px-6 md:px-10"
        style={{
          textAlign: "center",
          position: "relative",
          overflow: "hidden",
          background: `radial-gradient(ellipse 70% 55% at 50% 8%, ${C.cyan}1f 0%, transparent 72%)`,
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            backgroundImage: `linear-gradient(to bottom, transparent, ${C.bg} 94%), repeating-linear-gradient(0deg, ${C.border}14 0, ${C.border}14 1px, transparent 1px, transparent 56px), repeating-linear-gradient(90deg, ${C.border}12 0, ${C.border}12 1px, transparent 1px, transparent 56px)`,
          }}
        />

        <div className="relative z-10 max-w-5xl mx-auto py-10 md:py-16">
          <h1
            style={{
              color: C.t1,
              fontWeight: 900,
              letterSpacing: "-0.04em",
              lineHeight: 1.05,
              fontSize: "clamp(2.4rem, 6vw, 4.8rem)",
              marginBottom: 18,
            }}
          >
            Build, Test, and Deploy Crypto Trading Strategies Without Writing Code
          </h1>

          <p
            style={{
              color: C.t2,
              fontSize: 15,
              margin: "0 auto 40px",
              maxWidth: 720,
              lineHeight: 1.6,
              letterSpacing: "0.01em",
            }}
          >
            VyomQuant is the ultimate no-code platform for algorithmic traders. Create complex logic blocks, backtest with historical tick data, validate with Monte Carlo and Walk Forward analysis, and deploy live or paper bots in one click.
          </p>

          <div style={{ display: "flex", justifyContent: "center", gap: 16, flexWrap: "wrap" }}>
            <button
              onClick={() => go("auth-signup")}
              style={{
                background: C.accent,
                color: "#000",
                borderRadius: 12,
                padding: "14px 28px",
                fontSize: 13,
                fontWeight: 900,
                letterSpacing: "0.05em",
                textTransform: "uppercase",
                border: `1px solid ${C.accent}`,
                cursor: "pointer",
                boxShadow: `${C.glow.accent}, 0 4px 20px rgba(0,0,0,0.3)`,
                transition: "all 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
                animation: "ctaPulse 3s ease-in-out infinite"
              }}
              onMouseEnter={(e) => {
                e.target.style.transform = "translateY(-2px) scale(1.02)";
                e.target.style.boxShadow = `${C.glow.accent}, 0 8px 30px rgba(0,0,0,0.4)`;
              }}
              onMouseLeave={(e) => {
                e.target.style.transform = "translateY(0) scale(1)";
                e.target.style.boxShadow = `${C.glow.accent}, 0 4px 20px rgba(0,0,0,0.3)`;
              }}
            >
              <style>{`
                @keyframes ctaPulse {
                  0%, 100% { box-shadow: 0 0 20px ${C.accent}60, 0 0 40px ${C.accent}30, 0 4px 20px rgba(0,0,0,0.3); }
                  50% { box-shadow: 0 0 30px ${C.accent}80, 0 0 60px ${C.accent}50, 0 4px 20px rgba(0,0,0,0.3); }
                }
              `}</style>
              Start Free
            </button>

            <button
              onClick={() => setShowDemoModal(true)}
              style={{
                background: "transparent",
                color: C.t1,
                borderRadius: 12,
                padding: "14px 28px",
                fontSize: 13,
                fontWeight: 900,
                letterSpacing: "0.05em",
                textTransform: "uppercase",
                border: `1px solid ${C.border}`,
                cursor: "pointer",
                transition: "all 0.2s"
              }}
              onMouseEnter={(e) => {
                e.target.style.background = `${C.border}44`;
                e.target.style.transform = "translateY(-2px)";
              }}
              onMouseLeave={(e) => {
                e.target.style.background = "transparent";
                e.target.style.transform = "translateY(0)";
              }}
            >
              Book Demo
            </button>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-20 px-6 md:px-10" style={{ borderTop: `1px solid ${C.border}` }}>
        <div className="max-w-6xl mx-auto">
          <div style={{ textAlign: "center", marginBottom: 50 }}>
            <h2 style={{ color: C.t1, fontSize: 32, fontWeight: 900, marginBottom: 12 }}>Platform Features</h2>
            <p style={{ color: C.t2, fontSize: 14, fontFamily: "monospace" }}>Everything you need to run institutional-grade strategies</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {features.map((f, i) => (
              <LandingFeatureCard key={f.t} f={f} i={i} />
            ))}
          </div>
        </div>
      </section>

      {/* Pricing Section */}
      <section className="py-20 px-6 md:px-10" style={{ borderTop: `1px solid ${C.border}`, background: C.bg0 }}>
        <div className="max-w-6xl mx-auto">
          <div style={{ textAlign: "center", marginBottom: 50 }}>
            <h2 style={{ color: C.t1, fontSize: 32, fontWeight: 900, marginBottom: 12 }}>Simple Pricing Tiers</h2>
            <p style={{ color: C.t2, fontSize: 14, fontFamily: "monospace" }}>Pay as you scale your quantitative pipeline</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            {pricingPlans.map((plan) => (
              <LandingPricingCard key={plan.name} plan={plan} go={go} />
            ))}
          </div>
        </div>
      </section>

      {/* Testimonials Section */}
      <section className="py-20 px-6 md:px-10" style={{ borderTop: `1px solid ${C.border}` }}>
        <div className="max-w-6xl mx-auto">
          <div style={{ textAlign: "center", marginBottom: 50 }}>
            <h2 style={{ color: C.t1, fontSize: 32, fontWeight: 900, marginBottom: 12 }}>Algorithmic Trader Reviews</h2>
            <p style={{ color: C.t2, fontSize: 14, fontFamily: "monospace" }}>See how quants deploy strategy systems with VyomQuant</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {testimonials.map((t, i) => (
              <LandingTestimonialCard key={i} t={t} />
            ))}
          </div>
        </div>
      </section>

      {/* FAQ Section */}
      <section className="py-20 px-6 md:px-10" style={{ borderTop: `1px solid ${C.border}`, background: C.bg0 }}>
        <div className="max-w-4xl mx-auto">
          <div style={{ textAlign: "center", marginBottom: 50 }}>
            <h2 style={{ color: C.t1, fontSize: 32, fontWeight: 900, marginBottom: 12 }}>Frequently Asked Questions</h2>
            <p style={{ color: C.t2, fontSize: 14, fontFamily: "monospace" }}>Got questions? We've got answers.</p>
          </div>
          <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 18, padding: "24px 32px" }}>
            {faqs.map((faq, i) => (
              <LandingFAQItem key={i} q={faq.q} a={faq.a} />
            ))}
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer style={{
        borderTop: `1px solid ${C.border}`,
        padding: "40px 24px",
        background: C.bg0,
        textAlign: "center"
      }}>
        <div style={{
          maxWidth: "600px",
          margin: "0 auto",
          display: "flex",
          justifyContent: "center",
          gap: "24px",
          marginBottom: "16px"
        }}>
          <button onClick={() => go("legal")} style={{ background: "transparent", border: "none", color: C.t3, fontSize: "11px", fontFamily: "monospace", cursor: "pointer", transition: "color 0.2s" }} onMouseEnter={e => e.target.style.color = C.accent} onMouseLeave={e => e.target.style.color = C.t3}>Terms of Service</button>
          <button onClick={() => go("legal")} style={{ background: "transparent", border: "none", color: C.t3, fontSize: "11px", fontFamily: "monospace", cursor: "pointer", transition: "color 0.2s" }} onMouseEnter={e => e.target.style.color = C.accent} onMouseLeave={e => e.target.style.color = C.t3}>Privacy Policy</button>
          <button onClick={() => go("legal")} style={{ background: "transparent", border: "none", color: C.t3, fontSize: "11px", fontFamily: "monospace", cursor: "pointer", transition: "color 0.2s" }} onMouseEnter={e => e.target.style.color = C.accent} onMouseLeave={e => e.target.style.color = C.t3}>Risk Disclosure</button>
          <button onClick={() => go("legal")} style={{ background: "transparent", border: "none", color: C.t3, fontSize: "11px", fontFamily: "monospace", cursor: "pointer", transition: "color 0.2s" }} onMouseEnter={e => e.target.style.color = C.accent} onMouseLeave={e => e.target.style.color = C.t3}>Refund Policy</button>
        </div>
        <p style={{ color: C.t4, fontSize: "10px", fontFamily: "monospace" }}>&copy; {new Date().getFullYear()} VyomQuant. Simulated paper trading beta platform.</p>
      </footer>

      {/* Book Demo Modal */}
      {showDemoModal && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.75)",
            backdropFilter: "blur(4px)",
            zIndex: 1000,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 16
          }}
        >
          <div
            style={{
              background: C.bg2,
              border: `1px solid ${C.border}`,
              borderRadius: 18,
              padding: 32,
              width: 480,
              maxWidth: "100%",
              boxShadow: "0 40px 80px rgba(0,0,0,0.8)",
              position: "relative"
            }}
          >
            <button
              onClick={() => setShowDemoModal(false)}
              style={{
                position: "absolute",
                top: 16,
                right: 16,
                background: "transparent",
                border: "none",
                color: C.t3,
                fontSize: 20,
                cursor: "pointer",
                lineHeight: 1
              }}
              className="hover:text-white"
            >
              ×
            </button>
            <h2 style={{ color: C.t1, fontWeight: 900, fontSize: 20, marginBottom: 6 }}>Book a Private Demo</h2>
            <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginBottom: 24 }}>
              See how VyomQuant can automate your visual trading strategies at scale.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                alert("Demo request submitted! Our team will contact you shortly.");
                setShowDemoModal(false);
              }}
              style={{ display: "flex", flexDirection: "column", gap: 14 }}
            >
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase" }}>Name</label>
                <input required type="text" placeholder="John Doe" style={{ background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, borderRadius: 8, padding: "8px 12px", fontSize: 12, fontFamily: "monospace", outline: "none" }} />
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase" }}>Email</label>
                <input required type="email" placeholder="john@company.com" style={{ background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, borderRadius: 8, padding: "8px 12px", fontSize: 12, fontFamily: "monospace", outline: "none" }} />
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase" }}>Strategy Scale</label>
                <select style={{ background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, borderRadius: 8, padding: "8px 12px", fontSize: 12, fontFamily: "monospace", outline: "none" }}>
                  <option>Personal (1-5 bots)</option>
                  <option>Professional (5-20 bots)</option>
                  <option>Institutional (20+ bots)</option>
                </select>
              </div>
              <button type="submit" style={{ background: C.accent, color: "#000", border: `1px solid ${C.accent}`, borderRadius: 10, padding: "12px 0", fontWeight: 900, fontSize: 12, fontFamily: "monospace", textTransform: "uppercase", cursor: "pointer", marginTop: 8 }}>
                Request Access
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  PAGE: AUTH
// ═══════════════════════════════════════════════════════════════════
function AuthPage({ mode, go }) {
  const [showPw, setShowPw] = useState(false);
  const [authView, setAuthView] = useState(mode === "signup" ? "signup" : "signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loadingAction, setLoadingAction] = useState("");
  const [error, setError] = useState("");
  const [agreedToPolicies, setAgreedToPolicies] = useState(false);

  const isUp = authView === "signup";

  useEffect(() => {
    setAuthView(mode === "signup" ? "signup" : "signin");
    setError("");
  }, [mode]);

  const handleSignUp = async (e) => {
    e.preventDefault();
    if (!agreedToPolicies) {
      setError("You must agree to the Terms and Risk Disclosure.");
      return;
    }
    if (loadingAction) return;
    setError("");
    setLoadingAction("signup");

    console.log("🔐 AUTH DIAGNOSTIC: Starting signup flow", {
      email: email.trim(),
      timestamp: new Date().toISOString()
    });

    try {
      const { supabase } = await import('./supabase');
      console.log("🔐 AUTH DIAGNOSTIC: Supabase client loaded", {
        url: supabase.supabaseUrl,
        hasClient: !!supabase
      });

      const { data, error } = await supabase.auth.signUp({
        email: email.trim(),
        password: password,
      });

      console.log("🔐 AUTH DIAGNOSTIC: Supabase signup response", {
        hasData: !!data,
        hasError: !!error,
        hasSession: !!data?.session,
        hasUser: !!data?.user,
        error: error?.message
      });

      if (error) {
        throw error;
      }

      if (data?.session?.access_token) {
        sessionStorage.setItem("token", data.session.access_token);
        console.log("🔐 AUTH DIAGNOSTIC: Signup successful, token stored");
        go("dashboard");
      } else {
        console.log("🔐 AUTH DIAGNOSTIC: Signup successful, email verification required");
        setError("Registration successful. Please check your email to verify your account.");
      }
    } catch (err) {
      console.error("🔐 AUTH DIAGNOSTIC: Signup failed", {
        error: err.message,
        stack: err.stack,
        timestamp: new Date().toISOString()
      });
      setError(err.message || "Registration failed. Please check your inputs.");
    } finally {
      setLoadingAction("");
    }
  };


  const handleSignIn = async (e) => {
    e.preventDefault();
    if (loadingAction) return;
    setError("");
    setLoadingAction("signin");

    console.log("🔐 AUTH DIAGNOSTIC: Starting signin flow", {
      email: email.trim(),
      timestamp: new Date().toISOString()
    });

    try {
      const { supabase } = await import('./supabase');
      console.log("🔐 AUTH DIAGNOSTIC: Supabase client loaded", {
        url: supabase.supabaseUrl,
        hasClient: !!supabase
      });

      const { data, error } = await supabase.auth.signInWithPassword({
        email: email.trim(),
        password: password,
      });

      console.log("🔐 AUTH DIAGNOSTIC: Supabase signin response", {
        hasData: !!data,
        hasError: !!error,
        hasSession: !!data?.session,
        hasUser: !!data?.user,
        error: error?.message
      });

      if (error) {
        throw error;
      }

      if (data?.session?.access_token) {
        sessionStorage.setItem("token", data.session.access_token);
        console.log("🔐 AUTH DIAGNOSTIC: Signin successful, token stored");
        go("dashboard");
      } else {
        console.log("🔐 AUTH DIAGNOSTIC: Signin failed, no session");
        setError("Sign in failed. Please check your credentials.");
      }
    } catch (err) {
      console.error("🔐 AUTH DIAGNOSTIC: Signin failed", {
        error: err.message,
        stack: err.stack,
        timestamp: new Date().toISOString()
      });
      setError(err.message || "Sign in failed. Check your credentials.");
    } finally {
      setLoadingAction("");
    }
  };

  const handleGoogleAuth = async () => {
    if (loadingAction) return;
    setError("");
    setLoadingAction("google");
    try {
      // For now, show a message that Google auth requires configuration
      setError("Google authentication requires Supabase OAuth configuration. Please use email/password authentication.");
    } catch (err) {
      setError(err.message || "Google authentication failed.");
    } finally {
      setLoadingAction("");
    }
  };

  const handleForgotPassword = async () => {
    if (!email) {
      setError("Please enter your email address first.");
      return;
    }
    setLoadingAction("forgot-password");

    console.log("🔐 AUTH DIAGNOSTIC: Starting forgot password flow", {
      email: email.trim(),
      timestamp: new Date().toISOString()
    });

    try {
      const { supabase } = await import('./supabase');
      console.log("🔐 AUTH DIAGNOSTIC: Supabase client loaded", {
        url: supabase.supabaseUrl,
        hasClient: !!supabase
      });

      const { error } = await supabase.auth.resetPasswordForEmail(email.trim(), {
        redirectTo: `${window.location.origin}/reset-password`
      });

      console.log("🔐 AUTH DIAGNOSTIC: Supabase reset password response", {
        hasError: !!error,
        error: error?.message
      });

      if (error) {
        throw error;
      }

      console.log("🔐 AUTH DIAGNOSTIC: Password reset email sent successfully");
      setError("Password reset email sent successfully. Please check your inbox.");
    } catch (err) {
      console.error("🔐 AUTH DIAGNOSTIC: Password reset failed", {
        error: err.message,
        stack: err.stack,
        timestamp: new Date().toISOString()
      });
      setError(err.message || "Failed to send password reset email.");
    } finally {
      setLoadingAction("");
    }
  };
  const isLoading = !!loadingAction;

  return (
    <div style={{ background: C.bg0, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", position: "relative", overflow: "hidden" }}>
      <div style={{ position: "absolute", inset: 0, backgroundImage: `radial-gradient(ellipse 70% 70% at 20% 50%, rgba(0,212,255,0.05) 0%, transparent 60%),radial-gradient(ellipse 50% 50% at 80% 30%, rgba(139,92,246,0.04) 0%, transparent 55%)` }} />
      <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 18, padding: 36, width: 550, maxWidth: "calc(100vw - 32px)", position: "relative", zIndex: 1, boxShadow: "0 40px 80px rgba(0,0,0,0.7)" }}>
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <div style={{ background: "linear-gradient(135deg,#00d4ff,#0055ff)", borderRadius: 10, padding: "9px 10px", display: "inline-flex", marginBottom: 12 }}><Zap size={22} color="#000" strokeWidth={2.5} /></div>
          <h1 style={{ color: C.t1, fontWeight: 900, fontSize: 22, letterSpacing: -0.5 }}>
            {isUp ? "Create Your Account" : "Welcome Back"}
          </h1>
        </div>

        <>
          <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 8, marginBottom: 20 }}>
            <button
              type="button"
              onClick={handleGoogleAuth}
              disabled={isLoading}
              style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: "9px 0", display: "flex", alignItems: "center", justifyContent: "center", gap: 6, fontSize: 11, fontFamily: "monospace", color: C.t2, cursor: isLoading ? "not-allowed" : "pointer", transition: "all 0.15s", opacity: isLoading ? 0.6 : 1 }}
              className="hover:border-slate-500"
            >
              <Globe size={13} /> {loadingAction === "google" ? "Redirecting..." : "Google"}
            </button>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
            <div style={{ height: 1, flex: 1, background: C.border }} /><span style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }}>OR CONTINUE</span><div style={{ height: 1, flex: 1, background: C.border }} />
          </div>
        </>

        <form onSubmit={isUp ? handleSignUp : handleSignIn} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <Inp
            lbl="Email"
            ph="admin@algo22.io"
            type="email"
            icon={Mail}
            val={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={isLoading}
          />
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 3, textTransform: "uppercase" }}>Password</label>
            <div style={{ position: "relative" }}>
              <Lock size={12} style={{ color: C.t3, position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }} />
              <input type={showPw ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="•••••••••••" disabled={isLoading}
                style={{ background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, width: "100%", borderRadius: 8, padding: "8px 40px 8px 32px", fontSize: 12, fontFamily: "monospace", outline: "none", opacity: isLoading ? 0.6 : 1, cursor: isLoading ? "not-allowed" : "text" }}
                className="focus:border-cyan-500/50 transition-all" />
              <button type="button" onClick={() => setShowPw(!showPw)} style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", color: C.t3 }} className="hover:text-slate-400">
                {showPw ? <EyeOff size={13} /> : <Eye size={13} />}
              </button>
            </div>
          </div>
          {isUp ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
              <label style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 11, fontFamily: "monospace", color: C.t2, cursor: "pointer", lineHeight: "1.4" }}>
                <input
                  type="checkbox"
                  required
                  checked={agreedToPolicies}
                  onChange={(e) => setAgreedToPolicies(e.target.checked)}
                  style={{ accentColor: C.cyan, marginTop: 2 }}
                />
                <span>
                  I agree to the{" "}
                  <span onClick={(e) => { e.stopPropagation(); go("legal"); }} style={{ color: C.cyan, textDecoration: "underline", cursor: "pointer" }} className="hover:text-cyan-300">
                    Terms
                  </span>{" "}
                  and{" "}
                  <span onClick={(e) => { e.stopPropagation(); go("legal"); }} style={{ color: C.cyan, textDecoration: "underline", cursor: "pointer" }} className="hover:text-cyan-300">
                    Risk Disclosure
                  </span>.
                </span>
              </label>
            </div>
          ) : (
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, fontFamily: "monospace", color: C.t2, cursor: "pointer" }}>
                <input type="checkbox" style={{ accentColor: C.cyan }} /> Remember me
              </label>
              <span style={{ color: C.cyan, fontSize: 10, fontFamily: "monospace", cursor: "pointer" }} className="hover:underline" onClick={handleForgotPassword}>Forgot password?</span>
            </div>
          )}
          <Btn v="primary" sz="md" cls="w-full justify-center mt-1" disabled={isLoading}>
            {isUp ? (loadingAction === "signup" ? "Creating Account..." : "Create Account") : (loadingAction === "signin" ? "Signing In..." : "Sign In")}
          </Btn>
        </form>

        {!!error && (
          <div style={{ marginTop: 14, background: `${C.red}12`, border: `1px solid ${C.red}44`, color: C.red, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace" }}>
            {error}
          </div>
        )}

        <p style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", textAlign: "center", marginTop: 20 }}>
          {isUp ? "Already have an account? " : "No account yet? "}
          <span
            style={{ color: C.cyan, cursor: "pointer" }}
            className="hover:underline"
            onClick={() => {
              setAuthView(isUp ? "signin" : "signup");
              setError("");
            }}
          >
            {isUp ? "Sign in" : "Create one free"}
          </span>
        </p>

        <div style={{ marginTop: 24, borderTop: `1px solid ${C.border}`, paddingTop: 16, display: "flex", justifyContent: "center", gap: 16 }}>
          <span onClick={() => go("legal")} style={{ color: C.t3, fontSize: 9, cursor: "pointer", fontFamily: "monospace" }} className="hover:underline hover:text-white">Terms</span>
          <span onClick={() => go("legal")} style={{ color: C.t3, fontSize: 9, cursor: "pointer", fontFamily: "monospace" }} className="hover:underline hover:text-white">Privacy</span>
          <span onClick={() => go("legal")} style={{ color: C.t3, fontSize: 9, cursor: "pointer", fontFamily: "monospace" }} className="hover:underline hover:text-white">Risk Disclosure</span>
          <span onClick={() => go("legal")} style={{ color: C.t3, fontSize: 9, cursor: "pointer", fontFamily: "monospace" }} className="hover:underline hover:text-white">Refunds</span>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  PAGE: 2FA
// ═══════════════════════════════════════════════════════════════════
function TwoFA({ go }) {
  const [step, setStep] = useState(1);
  const [code, setCode] = useState(["", "", "", "", "", ""]);
  const refs = useRef([]);
  const onDigit = (i, v) => {
    if (!/^\d?$/.test(v)) return;
    const n = [...code]; n[i] = v; setCode(n);
    if (v && i < 5) refs.current[i + 1]?.focus();
  };
  return (
    <div style={{ background: C.bg0, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 18, padding: 36, width: 420 }}>
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <div style={{ background: "rgba(0,212,255,0.08)", border: "1px solid rgba(0,212,255,0.2)", borderRadius: "50%", width: 60, height: 60, display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 12 }}>
            <ShieldCheck size={26} style={{ color: C.cyan }} />
          </div>
          <h1 style={{ color: C.t1, fontWeight: 900, fontSize: 20 }}>Two-Factor Authentication</h1>
          <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginTop: 4 }}>{step === 1 ? "Set up TOTP on your authenticator app" : "Enter the 6-digit verification code"}</p>
        </div>
        {step === 1 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 12, padding: 16, display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
              {/* QR placeholder */}
              <div style={{ background: "#fff", padding: 12, borderRadius: 8, display: "grid", gridTemplateColumns: "repeat(10,8px)", gridTemplateRows: "repeat(10,8px)", gap: 1 }}>
                {Array.from({ length: 100 }, (_, i) => (
                  <div key={i} style={{ background: (i % 7 === 0 || i % 11 === 3 || i < 10 || i > 90 || i % 10 === 0 || i % 10 === 9) ? "#000" : "#fff", borderRadius: 1 }} />
                ))}
              </div>
              <span style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>Scan with Google Authenticator or Authy</span>
            </div>
            <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10, padding: 12 }}>
              <div style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", marginBottom: 6 }}>Or enter this setup key:</div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <code style={{ color: C.cyan, background: C.bg1, flex: 1, padding: "6px 10px", borderRadius: 6, fontSize: 12, fontFamily: "monospace" }}>JBSWY3DPEHPK3PXP</code>
                <Copy size={13} style={{ color: C.t3, cursor: "pointer" }} className="hover:text-cyan-400" />
              </div>
            </div>
            <Btn v="primary" cls="w-full justify-center" onClick={() => setStep(2)}>I've Added It — Verify Code →</Btn>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
              {code.map((d, i) => (
                <input key={i} ref={el => refs.current[i] = el} value={d} maxLength={1}
                  onChange={e => onDigit(i, e.target.value)}
                  style={{ background: C.bg3, border: `1px solid ${d ? C.cyan : C.border}`, color: C.t1, width: 46, height: 54, textAlign: "center", fontSize: 22, fontWeight: 900, borderRadius: 8, outline: "none", fontFamily: "monospace", transition: "all 0.15s" }} />
              ))}
            </div>
            <Btn v="primary" cls="w-full justify-center" onClick={() => go("wizard")}>Verify & Continue →</Btn>
            <button style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", textAlign: "center", cursor: "pointer" }} onClick={() => setStep(1)} className="hover:text-cyan-400">
              ← Back to QR code
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  PAGE: SETUP WIZARD
// ═══════════════════════════════════════════════════════════════════
function Wizard({ go }) {
  const [step, setStep] = useState(0);
  const steps = ["Secure Account", "Choose Plan", "Connect Exchange"];
  const plans = [
    { n: "Starter", p: 29, f: ["3 Active Bots", "5 Strategies", "Daily Backtest", "Email Alerts"] },
    { n: "Pro", p: 99, f: ["20 Active Bots", "Unlimited Strategies", "Realtime Backtest", "Telegram+Email", "ML Strategies"], best: true },
    { n: "Enterprise", p: 299, f: ["Unlimited Bots", "Priority Execution", "Dedicated Support", "Custom Strategies", "Full API"] },
  ];
  return (
    <div style={{ background: C.bg0, minHeight: "100vh", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: 32 }}>
      {/* Progress */}
      <div style={{ display: "flex", alignItems: "center", width: "100%", maxWidth: 560, marginBottom: 36 }}>
        {steps.map((s, i) => (
          <div key={s} style={{ display: "flex", alignItems: "center", flex: 1 }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
              <div style={{
                width: 30, height: 30, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
                background: i < step ? C.cyan : i === step ? "rgba(0,212,255,0.12)" : C.bg3,
                border: `2px solid ${i <= step ? C.cyan : C.border}`,
                color: i < step ? "#000" : i === step ? C.cyan : C.t3, fontWeight: 900, fontSize: 11, fontFamily: "monospace"
              }}>
                {i < step ? "✓" : i + 1}
              </div>
              <span style={{ color: i === step ? C.t1 : C.t3, fontSize: 9, fontFamily: "monospace", marginTop: 4, whiteSpace: "nowrap" }}>{s}</span>
            </div>
            {i < 2 && <div style={{ height: 2, flex: 1, background: i < step ? C.cyan : C.border, margin: "0 4px", marginBottom: 16 }} />}
          </div>
        ))}
      </div>

      <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 18, padding: 32, width: "100%", maxWidth: step === 1 ? 720 : 480 }}>
        {step === 0 && (
          <div>
            <h2 style={{ color: C.t1, fontWeight: 900, fontSize: 18, marginBottom: 4 }}>Secure Your Account</h2>
            <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginBottom: 20 }}>These settings protect your funds. Please complete all steps.</p>
            {[{ I: ShieldCheck, t: "2FA Enabled", d: "TOTP via Google Authenticator", ok: true }, { I: Mail, t: "Email Verified", d: "Confirmation sent to admin@algo22.io", ok: true }, { I: Bell, t: "Security Alerts", d: "Notify on new device logins", ok: false }].map(r => (
              <div key={r.t} style={{ background: C.bg3, border: `1px solid ${r.ok ? "rgba(0,255,136,0.15)" : C.border}`, borderRadius: 10, padding: 14, display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                <r.I size={15} style={{ color: r.ok ? C.green : C.t3 }} />
                <div style={{ flex: 1 }}>
                  <div style={{ color: C.t1, fontSize: 12, fontWeight: 700 }}>{r.t}</div>
                  <div style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>{r.d}</div>
                </div>
                {r.ok ? <CheckCircle size={15} style={{ color: C.green }} /> : <Btn v="outline" sz="xs">Enable</Btn>}
              </div>
            ))}
            <Btn v="primary" cls="w-full justify-center mt-4" onClick={() => setStep(1)}>Continue →</Btn>
          </div>
        )}
        {step === 1 && (
          <div>
            <h2 style={{ color: C.t1, fontWeight: 900, fontSize: 18, marginBottom: 4, textAlign: "center" }}>Choose Your Plan</h2>
            <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginBottom: 24, textAlign: "center" }}>14-day free trial on all plans. Upgrade or cancel anytime.</p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 14 }}>
              {plans.map(p => (
                <div key={p.n} style={{ background: p.best ? "rgba(0,212,255,0.05)" : C.bg3, border: `1px solid ${p.best ? C.cyan : C.border}`, borderRadius: 12, padding: 20, position: "relative", display: "flex", flexDirection: "column" }}>
                  {p.best && <div style={{ position: "absolute", top: -10, left: "50%", transform: "translateX(-50%)", background: C.cyan, color: "#000", fontSize: 8, fontWeight: 900, letterSpacing: 2, padding: "2px 10px", borderRadius: 20 }}>BEST VALUE</div>}
                  <div style={{ color: C.t1, fontWeight: 900, fontSize: 14 }}>{p.n}</div>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 3, margin: "10px 0" }}>
                    <span style={{ color: p.best ? C.cyan : C.t1, fontSize: 28, fontWeight: 900 }}>${p.p}</span>
                    <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>/mo</span>
                  </div>
                  <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 5, marginBottom: 14 }}>
                    {p.f.map(f => <div key={f} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10, fontFamily: "monospace", color: C.t2 }}><CheckCircle size={9} style={{ color: p.best ? C.cyan : C.green, flexShrink: 0 }} />{f}</div>)}
                  </div>
                  <Btn v={p.best ? "primary" : "outline"} sz="sm" cls="w-full justify-center" onClick={() => setStep(2)}>Select →</Btn>
                </div>
              ))}
            </div>
          </div>
        )}
        {step === 2 && (
          <div>
            <h2 style={{ color: C.t1, fontWeight: 900, fontSize: 18, marginBottom: 4 }}>Connect Your Exchange</h2>
            <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginBottom: 20 }}>Add API credentials to deploy bots. Keys are encrypted at rest.</p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 6, marginBottom: 16 }}>
              {["Binance", "Bybit", "OKX", "Kraken", "Coinbase", "KuCoin"].map(ex => (
                <button key={ex} style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 0", fontSize: 10, fontFamily: "monospace", fontWeight: 700, color: C.t2, cursor: "pointer", transition: "all 0.15s" }}
                  className="hover:border-cyan-500/40 hover:text-cyan-400">{ex}</button>
              ))}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 16 }}>
              <Inp lbl="API Key" ph="Paste your API key..." icon={Key} type="password" />
              <Inp lbl="Secret Key" ph="Paste your secret key..." icon={Lock} type="password" />
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <Btn v="outline" sz="sm" Icon={Wifi}>Test Connection</Btn>
              <Btn v="primary" cls="flex-1 justify-center" onClick={() => go("dashboard")}>Complete Setup →</Btn>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  METRIC CARD COMPONENT (for dashboard top bar)
// ═══════════════════════════════════════════════════════════════════

const MetricCard = ({ title, value, subValue, subValueColor, icon: Icon, loading = false, onClick }) => {
  if (loading) {
    return (
      <div style={{
        background: C.bg2,
        border: `1px solid ${C.border}`,
        borderRadius: C.radius.lg,
        padding: "12px 16px",
        minWidth: 140,
        display: "flex",
        flexDirection: "column",
        gap: 8
      }}>
        <SkeletonLine width="40%" height={9} />
        <SkeletonLine width="70%" height={18} />
        <SkeletonLine width="50%" height={12} />
      </div>
    );
  }

  return (
    <div
      onClick={onClick}
      style={{
        background: C.bg2,
        border: `1px solid ${C.border}`,
        borderRadius: C.radius.lg,
        padding: "12px 16px",
        minWidth: 140,
        display: "flex",
        flexDirection: "column",
        gap: 4,
        cursor: onClick ? "pointer" : "default",
        transition: "all 0.15s ease"
      }}
      onMouseEnter={(e) => onClick && (e.currentTarget.style.borderColor = C.borderLight)}
      onMouseLeave={(e) => onClick && (e.currentTarget.style.borderColor = C.border)}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {Icon && <Icon size={12} color={C.t3} />}
        <span style={{ color: C.t3, fontSize: 9, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>{title}</span>
      </div>
      <div style={{ color: C.t1, fontSize: 18, fontWeight: 700, fontFamily: "monospace" }}>{value}</div>
      {subValue && (
        <div style={{ color: subValueColor || C.t2, fontSize: 11, fontWeight: 500 }}>{subValue}</div>
      )}
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════
//  PAGE: DASHBOARD
// ═══════════════════════════════════════════════════════════════════
function Dashboard({ go }) {
  const [equityCurve, setEquityCurve] = useState([]);
  const [stats, setStats] = useState(null);
  const [recentTransactions, setRecentTransactions] = useState([]);
  const [isLoadingStats, setIsLoadingStats] = useState(true);
  const [isLoadingRecent, setIsLoadingRecent] = useState(true);
  const [isLoadingEquity, setIsLoadingEquity] = useState(true);
  const [activeBots, setActiveBots] = useState([]);
  const [isLoadingActiveBots, setIsLoadingActiveBots] = useState(true);
  // New state variables for upgraded dashboard
  const [strategyStatus, setStrategyStatus] = useState(null);
  const [activePositions, setActivePositions] = useState([]);
  const [totalStats, setTotalStats] = useState(null);

  useEffect(() => {
    // Utility functions for data normalization
    const colorByLabel = (label = "") => {
      const v = String(label).toLowerCase();
      if (v.includes("portfolio")) return "cyan";
      if (v.includes("p&l")) return "green";
      if (v.includes("bot")) return "purple";
      if (v.includes("capital")) return "orange";
      return "cyan";
    };

    const iconByLabel = (label = "") => {
      const v = String(label).toLowerCase();
      if (v.includes("portfolio")) return DollarSign;
      if (v.includes("p&l")) return TrendingUp;
      if (v.includes("bot")) return Bot;
      if (v.includes("capital")) return Gauge;
      return Activity;
    };

    const formatValue = (value) => (value === null || value === undefined ? "-" : String(value));

    const normalizeStats = (rows = []) =>
      (Array.isArray(rows) ? rows : []).map((row) => ({
        l: row.label ?? row.l ?? "Metric",
        v: formatValue(row.value ?? row.v),
        delta: row.delta ?? null,
        I: iconByLabel(row.label ?? row.l),
        c: colorByLabel(row.label ?? row.l),
      }));

    const normalizeRecent = (rows = []) =>
      (Array.isArray(rows) ? rows : []).map((row) => ({
        t: row.time ?? row.t ?? "-",
        p: row.pair ?? row.p ?? "-",
        s: String(row.side ?? row.s ?? "-").toUpperCase(),
        pr: formatValue(row.price ?? row.pr),
        a: formatValue(row.amount ?? row.a),
      }));

    const normalizeEquityCurve = (rows = []) =>
      (Array.isArray(rows) ? rows : []).map((row, index) => ({
        d: index,
        v: Number(row.value ?? row.equity ?? row.v ?? 0),
      }));

    const normalizeStatus = (value = "") => {
      const status = String(value).toLowerCase();
      if (["running", "active", "live", "started"].includes(status)) return "running";
      if (["paused", "pause"].includes(status)) return "paused";
      if (["backtesting", "testing"].includes(status)) return "backtesting";
      if (["stopped", "stop", "inactive"].includes(status)) return "stopped";
      return "running";
    };

    const toNumber = (value, fallback = 0) => {
      const num = Number(value);
      return Number.isFinite(num) ? num : fallback;
    };

    const normalizeActiveBots = (rows = []) =>
      (Array.isArray(rows) ? rows : []).map((row, idx) => ({
        id: row.id ?? row.strategy_id ?? `bot-${idx}`,
        name: row.name ?? row.strategy_name ?? row.bot_name ?? "Unnamed Strategy",
        pair: row.pair ?? row.symbol ?? "N/A",
        status: normalizeStatus(row.status),
        pnl: toNumber(row.pnl ?? row.pnl_percent ?? row.performance ?? row.return_pct, 0),
        wr: toNumber(row.wr ?? row.win_rate ?? row.winRate ?? 0, 0),
      }));

    // Load all data using Promise.allSettled for better error handling
    const loadDashboardData = async () => {
      try {
        setIsLoadingStats(true);
        setIsLoadingEquity(true);
        const apiStats = await endpoints.user.getStats();

        if (apiStats) {
          // Map snake_case to camelCase
          const mappedStats = {
            totalTrades: apiStats.total_trades ?? 0,
            totalPnl: apiStats.total_pnl ?? 0,
            winRate: apiStats.win_rate ?? 0,
            activeBots: apiStats.active_bots ?? 0,
            strategies: apiStats.total_strategies ?? 0,
          };
          setStats(mappedStats);
        } else {
          setStats(null);
        }

        // Load equity curve data
        const equityResponse = await get('/api/portfolio/equity-curve', { params: { days: 90 } });
        if (!equityResponse) {
          setEquityCurve([]);
        } else {
          const equityData = Array.isArray(equityResponse?.data) ? equityResponse.data : [];
          const normalizedEquity = normalizeEquityCurve(equityData);
          setEquityCurve(normalizedEquity);
        }
      } catch (error) {
        setStats(null);
        setEquityCurve([]);
      } finally {
        setIsLoadingStats(false);
        setIsLoadingEquity(false);
      }
    };

    loadDashboardData();
  }, []);

  useEffect(() => {
    // WebSocket temporarily disabled

    // Cleanup on unmount
    return () => {
      // No cleanup needed since WebSocket is disabled
    };
  }, []);

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      {/* Top Stats - Premium Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, marginBottom: 16 }}>
        {/* Total Portfolio */}
        <PremiumCard
          glowOnHover
          glowColor="accent"
          borderAccent
          style={{ position: "relative", overflow: "hidden" }}
        >
          <div style={{
            position: "absolute",
            top: 0,
            right: 0,
            width: 100,
            height: 100,
            background: C.gradient.accent,
            opacity: 0.3,
            borderRadius: "0 0 0 100%"
          }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Total Portfolio</span>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <MiniSparkline data={equityCurve.slice(-20).map(d => d.v)} width={60} height={16} color={C.accent} />
              <DollarSign size={13} style={{ color: C.accent }} />
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            {isLoadingStats ? (
              <SkeletonLine width="70%" height={24} />
            ) : (
              <>
                <span style={{ color: C.t1, fontSize: 22, fontWeight: 900, fontFamily: "monospace", textShadow: C.glow.accent }}>
                  $<AnimatedNumber value={stats?.totalPnl || 0} duration={800} />
                </span>
                <StatusDot status="live" size={6} />
              </>
            )}
          </div>
          {!isLoadingStats && stats?.totalPnl > 0 && (
            <div style={{ marginTop: 4 }}>
              <PnLBadge value={stats?.totalPnl || 0} size="sm" />
            </div>
          )}
        </PremiumCard>

        {/* 24H P&L */}
        <PremiumCard
          glowOnHover
          glowColor={stats?.totalPnl >= 0 ? "profit" : "loss"}
          borderAccent
          style={{ position: "relative", overflow: "hidden" }}
        >
          <div style={{
            position: "absolute",
            top: 0,
            right: 0,
            width: 100,
            height: 100,
            background: stats?.totalPnl >= 0 ? C.gradient.profit : C.gradient.loss,
            opacity: 0.3,
            borderRadius: "0 0 0 100%"
          }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>24H P&L</span>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <TrendingUp size={13} style={{ color: stats?.totalPnl >= 0 ? C.profit : C.loss }} />
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            {isLoadingStats ? (
              <SkeletonLine width="70%" height={24} />
            ) : (
              <PnLBadge value={stats?.totalPnl || 0} size="lg" animated />
            )}
          </div>
          {!isLoadingStats && (
            <div style={{ marginTop: 8, fontSize: 10, color: C.t3, fontFamily: "monospace" }}>
              {stats?.totalPnl >= 0 ? "▲ Up from yesterday" : "▼ Down from yesterday"}
            </div>
          )}
        </PremiumCard>

        {/* Active Bots */}
        <PremiumCard
          glowOnHover
          glowColor="purple"
          borderAccent
          style={{ position: "relative", overflow: "hidden" }}
        >
          <div style={{
            position: "absolute",
            top: 0,
            right: 0,
            width: 100,
            height: 100,
            background: "linear-gradient(135deg, rgba(124,77,255,0.15) 0%, rgba(124,77,255,0.05) 100%)",
            opacity: 0.3,
            borderRadius: "0 0 0 100%"
          }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Active Bots</span>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <StatusDot status={stats?.activeBots > 0 ? "active" : "idle"} size={6} />
              <Bot size={13} style={{ color: C.purple }} />
            </div>
          </div>
          <div style={{ color: C.t1, fontSize: 22, fontWeight: 900, fontFamily: "monospace" }}>
            {isLoadingStats ? (
              <SkeletonLine width="40%" height={24} />
            ) : (
              <AnimatedNumber value={stats?.activeBots || 0} suffix=" bots" />
            )}
          </div>
          {!isLoadingStats && stats?.activeBots > 0 && (
            <div style={{ marginTop: 8, fontSize: 10, color: C.t3, fontFamily: "monospace" }}>
              {stats?.strategies || 0} strategies deployed
            </div>
          )}
        </PremiumCard>

        {/* Win Rate */}
        <PremiumCard
          glowOnHover
          glowColor="gold"
          borderAccent
          style={{ position: "relative", overflow: "hidden" }}
        >
          <div style={{
            position: "absolute",
            top: 0,
            right: 0,
            width: 100,
            height: 100,
            background: "linear-gradient(135deg, rgba(255,214,0,0.15) 0%, rgba(255,214,0,0.05) 100%)",
            opacity: 0.3,
            borderRadius: "0 0 0 100%"
          }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Win Rate</span>
            <Gauge size={13} style={{ color: C.warning }} />
          </div>
          {isLoadingStats ? (
            <SkeletonLine width="60%" height={24} />
          ) : (
            <>
              <div style={{ color: C.t1, fontSize: 22, fontWeight: 900, fontFamily: "monospace" }}>
                <AnimatedNumber value={stats?.winRate || 0} suffix="%" />
              </div>
              <div style={{ marginTop: 8 }}>
                <RiskMeter
                  value={stats?.winRate || 0}
                  max={100}
                  label="Performance"
                  warningAt={0.5}
                  dangerAt={0.3}
                />
              </div>
            </>
          )}
        </PremiumCard>
      </div>

      {/* Upgraded Dashboard Components */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 12, marginBottom: 12 }}>
        {/* System Status - NEW */}
        <SystemStatus status={strategyStatus} />

        {/* Performance Metrics - NEW */}
        <PerformanceMetrics metrics={{
          total_trades: totalStats?.total_trades || 0,
          winning_trades: totalStats?.winning_trades || 0,
          losing_trades: totalStats?.losing_trades || 0,
          win_rate_pct: totalStats?.win_rate || 0,
          avg_trade_return_pct: totalStats?.avg_trade_return_pct || 0,
          sharpe_ratio: totalStats?.sharpe_ratio || 0,
          max_drawdown_pct: totalStats?.max_drawdown_pct || 0,
          profit_factor: totalStats?.profit_factor || 0,
          total_return_pct: totalStats?.total_return_pct || 0,
        }} />
      </div>

      {/* Equity Curve + Drawdown + Live Positions */}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12, marginBottom: 12 }}>
        {/* Performance & Equity Curve Chart */}
        <Card cls="p-4">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ color: C.t1, fontWeight: 900, fontSize: 14 }}>Performance & Equity Curve</span>
              <Tag2 c="cyan">Portfolio Value</Tag2>
              <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>HISTORICAL</span>
            </div>
            <div style={{ display: "flex", gap: 4 }}>
              {["1D", "1W", "1M", "ALL"].map(tf => (
                <button key={tf} style={{ background: tf === "1M" ? "rgba(0,212,255,0.12)" : "transparent", color: tf === "1M" ? C.cyan : C.t3, border: `1px solid ${tf === "1M" ? C.cyan + "40" : C.border}`, borderRadius: 5, padding: "3px 7px", fontSize: 9, fontFamily: "monospace", cursor: "pointer" }}>{tf}</button>
              ))}
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 12 }}>
            <span style={{ color: C.cyan, fontSize: 24, fontWeight: 900 }}>
              {isLoadingStats ? "$0.00" : `$${(stats?.totalPnl || 0).toFixed(2)}`}
            </span>
            {!isLoadingStats && stats?.totalPnl > 0 && (
              <span style={{ color: C.green, fontSize: 11, fontFamily: "monospace" }}>
                ▲ {stats?.totalPnl?.toFixed(2)} ({stats?.winRate?.toFixed(1) || "0"}%)
              </span>
            )}
          </div>
          {isLoadingEquity ? (
            <div style={{ height: 180, display: "flex", alignItems: "center", justifyContent: "center", color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
              Loading equity curve...
            </div>
          ) : equityCurve.length > 0 ? (
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={equityCurve}>
                <defs>
                  <linearGradient id="cg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={C.cyan} stopOpacity={0.15} />
                    <stop offset="95%" stopColor={C.cyan} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="d" hide />
                <YAxis domain={["auto", "auto"]} hide />
                <Tooltip content={<CustomTooltip />} />
                <Area dataKey="v" stroke={C.cyan} strokeWidth={1.5} fill="url(#cg)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ height: 180, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <EmptyState
                icon={Activity}
                title="Waiting for market data..."
                subtitle="Deploy strategies to see your equity curve"
                hint="Deploy a strategy to begin collecting performance data"
                size="sm"
              />
            </div>
          )}
        </Card>

        {/* Drawdown Chart - NEW */}
        <DrawdownChart
          data={equityCurve.map((d, i, arr) => ({
            timestamp: new Date().toISOString(),
            drawdown_pct: i > 0 ? Math.max(0, (1 - d.v / Math.max(...arr.slice(0, i + 1).map(e => e.v))) * 100) : 0
          }))}
          height={200}
        />

        {/* Live Positions - NEW */}
        <LivePositions positions={activePositions} />

        {/* Active Strategies panel */}
        <Card cls="p-4">
          <PanelTitle title="Active Strategies" right={<Btn v="ghost" sz="xs" onClick={() => go("strategies")}>View All</Btn>} />
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {isLoadingActiveBots && <div style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", padding: "6px 2px" }}>Loading active strategies...</div>}
            {!isLoadingActiveBots && Array.isArray(activeBots) && activeBots.map(s => (
              <div key={s.id} style={{ background: C.bg3, borderRadius: 8, padding: 10 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ color: C.t1, fontSize: 10, fontWeight: 700 }}>{s.name}</span>
                  <span style={{ color: s.pnl >= 0 ? C.green : C.red, fontSize: 10, fontFamily: "monospace", fontWeight: 700 }}>{s.pnl >= 0 ? "+" : ""}{s.pnl}%</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <Tag2 c="cyan">{s.pair}</Tag2>
                  <StatusDot status={s.status} />
                </div>
                <div style={{ marginTop: 6 }}>
                  <ProgressBar v={s.wr} max={100} color={s.pnl >= 0 ? C.green : C.red} h={3} />
                </div>
              </div>
            ))}
            {!isLoadingActiveBots && activeBots.length === 0 && <div style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", padding: "6px 2px" }}>No running strategies.</div>}
          </div>
        </Card>
      </div>

      {/* Recent Transactions */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 12 }}>
        <Card cls="p-4">
          <PanelTitle title="Recent Transactions" right={<Btn v="ghost" sz="xs" onClick={() => go("history")}>Full Ledger →</Btn>} />
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
            <thead>
              <tr style={{ color: C.t3, letterSpacing: 2, fontSize: 9 }}>
                {["TIME", "PAIR", "SIDE", "PRICE", "AMOUNT"].map(h => (
                  <th key={h} style={{ textAlign: "left", padding: "4px 8px", fontWeight: 900 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoadingRecent && (
                <tr>
                  <td colSpan={5} style={{ padding: "10px 8px", color: C.t3, fontFamily: "monospace" }}>Loading recent transactions...</td>
                </tr>
              )}
              {!isLoadingRecent && recentTransactions.length === 0 && (
                <tr>
                  <td colSpan={5} style={{ padding: "10px 8px", color: C.t3, fontFamily: "monospace", textAlign: "center" }}>No recent transactions found.</td>
                </tr>
              )}
              {Array.isArray(recentTransactions) && recentTransactions.map((r, i) => (
                <tr key={i} style={{ borderTop: `1px solid ${C.border}22` }} className="hover:bg-white/5 transition-colors">
                  <td style={{ padding: "6px 8px", color: C.t3 }}>{r.t}</td>
                  <td style={{ padding: "6px 8px", color: C.t1, fontWeight: 700 }}>{r.p}</td>
                  <td style={{ padding: "6px 8px" }}><Tag2 c={r.s === "BUY" ? "green" : "red"}>{r.s}</Tag2></td>
                  <td style={{ padding: "6px 8px", color: C.t1 }}>{r.pr}</td>
                  <td style={{ padding: "6px 8px", color: C.t2 }}>{r.a}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  PAGE: STRATEGIES
// ═══════════════════════════════════════════════════════════════════
function Strategies({ go, resumeBuilderStrategy, onResumeBuilderConsumed }) {
  const [view, setView] = useState("library");
  const [sel, setSel] = useState(null);
  const [strategies, setStrategies] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isProcessing, setIsProcessing] = useState({});
  const [editingStrategy, setEditingStrategy] = useState(null);
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterStatus, setFilterStatus] = useState("all");

  useEffect(() => {
    const controller = new AbortController();
    const API_BASE = import.meta.env.VITE_API_BASE_URL || "https://api.algo22.io";

    const toNumber = (v, fallback = 0) => {
      const n = Number(v);
      return Number.isFinite(n) ? n : fallback;
    };

    const normalizeStatus = (value = "") => {
      const s = String(value).toLowerCase();
      if (["running", "active", "live", "started"].includes(s)) return "running";
      if (["paused", "pause"].includes(s)) return "paused";
      if (["backtesting", "testing"].includes(s)) return "backtesting";
      if (["stopped", "inactive", "stop"].includes(s)) return "stopped";
      return "stopped";
    };

    const normalizeStrategies = (rows = []) =>
      (Array.isArray(rows) ? rows : []).map((row, i) => ({
        id: row.id ?? row.strategy_id ?? i + 1,
        name: row.name ?? row.strategy_name ?? `Strategy #${i + 1}`,
        pair: row.pair ?? row.symbol ?? "N/A",
        status: normalizeStatus(row.status),
        pnl: toNumber(row.pnl ?? row.pnl_percent ?? row.return_pct, 0),
        wr: toNumber(row.wr ?? row.win_rate ?? row.winRate, 0),
        dd: toNumber(row.dd ?? row.max_dd ?? row.maxDrawdown, 0),
        tf: row.tf ?? row.timeframe ?? "N/A",
        type: row.type ?? row.strategy_type ?? "Custom",
      }));

    const loadStrategies = async () => {
      try {
        setIsLoading(true);
        console.log("📊 API CALL: GET /api/strategies");
        const payload = await endpoints.strategies.list();
        console.log("📊 API RESPONSE:", payload);
        const rows = Array.isArray(payload) ? payload : payload?.data || payload?.strategies || [];
        if (Array.isArray(rows)) setStrategies(normalizeStrategies(rows));
      } catch (err) {
        console.error("📊 API ERROR: Failed to load strategies:", err.message);
      } finally {
        setIsLoading(false);
      }
    };

    loadStrategies();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!resumeBuilderStrategy) return;
    setEditingStrategy(resumeBuilderStrategy);
    setView("builder");
    if (onResumeBuilderConsumed) onResumeBuilderConsumed();
  }, [resumeBuilderStrategy]);

  const totalStrategies = Array.isArray(strategies) ? strategies.length : 0;
  const runningStrategies = Array.isArray(strategies) ? strategies.filter((s) => s.status === "running").length : 0;
  const totalPnl = Array.isArray(strategies) ? strategies.reduce((sum, s) => sum + (Number.isFinite(Number(s.pnl)) ? Number(s.pnl) : 0), 0) : 0;
  const avgWinRate = totalStrategies
    ? strategies.reduce((sum, s) => sum + (Number.isFinite(Number(s.wr)) ? Number(s.wr) : 0), 0) / totalStrategies
    : 0;
  const visibleStrategies = Array.isArray(strategies) ? strategies.filter((s) => filterStatus === "all" || s.status === filterStatus) : [];
  const API_BASE = import.meta.env.VITE_API_BASE_URL || "https://api.algo22.io";

  const setProcessingFor = (id, value) =>
    setIsProcessing((prev) => ({ ...prev, [id]: value }));

  const handleDeployStrategy = async (id) => {
    if (isProcessing[id]) return;
    const prevStrategies = strategies;
    setProcessingFor(id, true);
    setStrategies((prev) => Array.isArray(prev) ? prev.map((s) => (s.id === id ? { ...s, status: "running" } : s)) : prev);
    try {
      console.log(`📊 API CALL: POST /api/strategies/${id}/deploy`);
      const res = await endpoints.strategies.deploy(id);
      console.log("📊 API RESPONSE:", res);
    } catch (err) {
      console.error("📊 API ERROR:", err.message);
      setStrategies(prevStrategies);
    } finally {
      setProcessingFor(id, false);
    }
  };

  const handlePauseStrategy = async (id) => {
    if (isProcessing[id]) return;
    const prevStrategies = strategies;
    setProcessingFor(id, true);
    setStrategies((prev) => Array.isArray(prev) ? prev.map((s) => (s.id === id ? { ...s, status: "paused" } : s)) : prev);
    try {
      console.log(`📊 API CALL: POST /api/strategies/${id}/pause`);
      const res = await endpoints.strategies.pause(id);
      console.log("📊 API RESPONSE:", res);
    } catch (err) {
      console.error("📊 API ERROR:", err.message);
      setStrategies(prevStrategies);
    } finally {
      setProcessingFor(id, false);
    }
  };

  const handleDeleteStrategy = async (id) => {
    if (isProcessing[id]) return;
    const prevStrategies = strategies;
    setProcessingFor(id, true);
    setStrategies((prev) => prev.filter((s) => s.id !== id));
    try {
      console.log(`📊 API CALL: DELETE /api/strategies/${id}`);
      const res = await endpoints.strategies.delete(id);
      console.log("📊 API RESPONSE:", res);
    } catch (err) {
      console.error("📊 API ERROR:", err.message);
      setStrategies(prevStrategies);
    } finally {
      setProcessingFor(id, false);
    }
  };

  if (view === "builder") return <StrategyBuilder onBack={() => setView("library")} strategy={editingStrategy} onBacktest={(payload) => go("backtest", payload)} />;

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      {isLoading && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: C.t3, fontFamily: "monospace" }}>
          Loading strategies...
        </div>
      )}
      {!isLoading && (
        <>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
            <div>
              <h1 style={{ color: C.t1, fontWeight: 900, fontSize: 20, letterSpacing: -0.5 }}>Strategy Library</h1>
              <p style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", marginTop: 3 }}>Manage, backtest, and deploy your algorithmic strategies</p>
            </div>
            <div style={{ display: "flex", gap: 8, position: "relative" }}>
              <Btn v="outline" sz="sm" Icon={Filter} onClick={() => setFilterOpen(v => !v)}>Filter</Btn>
              {filterOpen && (
                <div style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 20, background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 8, padding: 6, minWidth: 130 }}>
                  {[
                    { id: "all", label: "All" },
                    { id: "running", label: "Running" },
                    { id: "paused", label: "Paused" },
                  ].map(opt => (
                    <button
                      key={opt.id}
                      onClick={() => {
                        setFilterStatus(opt.id);
                        setFilterOpen(false);
                      }}
                      style={{ width: "100%", textAlign: "left", background: filterStatus === opt.id ? C.cyan + "18" : "transparent", color: filterStatus === opt.id ? C.cyan : C.t2, border: `1px solid ${filterStatus === opt.id ? C.cyan + "30" : "transparent"}`, borderRadius: 6, padding: "5px 8px", fontSize: 10, fontFamily: "monospace", cursor: "pointer", marginBottom: 4 }}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              )}
              <Btn v="primary" sz="sm" Icon={Plus} onClick={() => setView("builder")}>New Strategy</Btn>
            </div>
          </div>

          {/* Stats row */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 16 }}>
            {[
              { l: "Total Strategies", v: String(totalStrategies), I: Layers, c: C.cyan },
              { l: "Running", v: String(runningStrategies), I: Radio, c: C.green },
              { l: "Total P&L", v: `${totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(2)}%`, I: TrendingUp, c: totalPnl >= 0 ? C.green : C.red },
              { l: "Avg Win Rate", v: `${avgWinRate.toFixed(1)}%`, I: Target, c: C.purple },
            ].map(s => (
              <Card key={s.l} cls="p-4">
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                  <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>{s.l}</span>
                  <s.I size={12} style={{ color: s.c }} />
                </div>
                <div style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>{s.v}</div>
              </Card>
            ))}
          </div>

          {/* Strategy Cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12 }}>
            {Array.isArray(visibleStrategies) && visibleStrategies.map(s => (
              <Card key={s.id} cls="p-4 hover:border-cyan-500/20 transition-all cursor-pointer" onClick={() => setSel(sel === s.id ? null : s.id)}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <StatusDot status={s.status} />
                    <span style={{ color: C.t1, fontWeight: 900, fontSize: 12 }}>{s.name}</span>
                  </div>
                  <Tag2 c={s.status === "running" ? "green" : s.status === "backtesting" ? "cyan" : s.status === "paused" ? "orange" : "red"}>
                    {s.status}
                  </Tag2>
                </div>
                <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
                  <Tag2 c="cyan">{s.pair}</Tag2>
                  <Tag2 c="purple">{s.type}</Tag2>
                  <Tag2 c="gold">{s.tf}</Tag2>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 6, marginBottom: 10 }}>
                  {[
                    { l: "P&L", v: `${s.pnl >= 0 ? "+" : ""}${s.pnl}%`, c: s.pnl >= 0 ? C.green : C.red },
                    { l: "Win Rate", v: `${s.wr}%`, c: C.cyan },
                    { l: "Max DD", v: `${s.dd}%`, c: C.red },
                  ].map(m => (
                    <div key={m.l} style={{ background: C.bg3, borderRadius: 6, padding: "6px 8px", textAlign: "center" }}>
                      <div style={{ color: C.t3, fontSize: 8, fontFamily: "monospace", letterSpacing: 2, marginBottom: 2 }}>{m.l}</div>
                      <div style={{ color: m.c, fontSize: 12, fontWeight: 900, fontFamily: "monospace" }}>{m.v}</div>
                    </div>
                  ))}
                </div>
                <ProgressBar v={s.wr} max={100} color={s.pnl >= 0 ? C.green : C.red} h={3} />
                <div style={{ display: "flex", gap: 4, marginTop: 10 }}>
                  <Btn v="ghost" sz="xs" Icon={Edit2} onClick={e => { e.stopPropagation(); setEditingStrategy(s); setView("builder"); }} disabled={!!isProcessing[s.id]}>Edit</Btn>
                  <Btn v="ghost" sz="xs" Icon={BarChart2} onClick={e => e.stopPropagation()} disabled={!!isProcessing[s.id]}>Backtest</Btn>
                  {s.status === "running"
                    ? <Btn v="ghost" sz="xs" Icon={Pause} onClick={e => { e.stopPropagation(); handlePauseStrategy(s.id); }} disabled={!!isProcessing[s.id]}>Pause</Btn>
                    : <Btn v="success" sz="xs" Icon={Play} onClick={e => { e.stopPropagation(); handleDeployStrategy(s.id); }} disabled={!!isProcessing[s.id]}>Run</Btn>}
                  <Btn v="danger" sz="xs" Icon={Trash2} cls="ml-auto" onClick={e => { e.stopPropagation(); handleDeleteStrategy(s.id); }} disabled={!!isProcessing[s.id]} />
                </div>
              </Card>
            ))}
            {/* Add new card */}
            <div style={{ background: C.bg1, border: `2px dashed ${C.border}`, borderRadius: 12, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8, padding: 32, cursor: "pointer", minHeight: 220, transition: "all 0.2s" }}
              onClick={() => setView("builder")} className="hover:border-cyan-500/30 hover:bg-cyan-500/3">
              <PlusCircle size={24} style={{ color: C.t4 }} />
              <span style={{ color: C.t3, fontSize: 11, fontFamily: "monospace" }}>Create New Strategy</span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  CCXT DATA PIPELINE - Production Grade Integration
// ═══════════════════════════════════════════════════════════════════

// Data Pipeline Context
const DataPipelineContext = createContext(null);

const useDataPipeline = () => {
  const context = useContext(DataPipelineContext);
  if (!context) throw new Error("useDataPipeline must be used within DataPipelineProvider");
  return context;
};

const DataPipelineProvider = ({ children, mode = 'backtest' }) => {
  // Mode: 'backtest' | 'live'
  const [pipelineMode, setPipelineMode] = useState(mode);

  // Exchange from vault (no user selection needed)
  const [activeExchange, setActiveExchange] = useState(null);
  const [isLoadingExchange, setIsLoadingExchange] = useState(true);
  const [availableTimeframes, setAvailableTimeframes] = useState(["1m", "5m", "15m", "1h", "4h", "1d"]);

  // Symbol discovery
  const [availableSymbols, setAvailableSymbols] = useState([]);
  const [symbolSearchQuery, setSymbolSearchQuery] = useState("");
  const [isLoadingSymbols, setIsLoadingSymbols] = useState(false);

  // OHLCV data cache
  const [ohlcCache, setOhlcCache] = useState(new Map());
  const [isFetchingData, setIsFetchingData] = useState(false);
  const [fetchProgress, setFetchProgress] = useState(0);

  // Validation state
  const [validationErrors, setValidationErrors] = useState([]);
  const [dataAvailability, setDataAvailability] = useState({});

  // WebSocket for live mode
  const [liveData, setLiveData] = useState(null);
  const [isLiveConnected, setIsLiveConnected] = useState(false);

  // Fetch connected exchange from vault
  useEffect(() => {
    const fetchActiveExchange = async () => {
      setIsLoadingExchange(true);
      try {
        // Try to fetch from user's exchange vault/connected accounts
        const accounts = await endpoints.exchange?.getAccounts?.() ||
          await endpoints.user?.getConnectedExchanges?.() ||
          await get('/api/exchange/accounts');

        if (accounts && accounts.length > 0) {
          // Get first active exchange
          const active = accounts.find(a => a.status === 'active' || a.is_connected) || accounts[0];
          setActiveExchange({
            id: active.id,
            name: active.exchange || active.name || 'binance',
            apiKey: active.api_key_present || active.has_api_key,
            isTestnet: active.is_testnet || false
          });
        } else {
          // Fallback to binance if no connected exchange
          setActiveExchange({ id: 'default', name: 'binance', isDefault: true });
        }
      } catch (err) {
        console.warn("Failed to fetch exchange accounts, using fallback:", err);
        setActiveExchange({ id: 'default', name: 'binance', isDefault: true });
      } finally {
        setIsLoadingExchange(false);
      }
    };

    fetchActiveExchange();
  }, []);

  // Load markets from CCXT
  const loadMarkets = useCallback(async (exchangeName = null) => {
    const exchange = exchangeName || activeExchange?.name || 'binance';
    setIsLoadingSymbols(true);

    try {
      // Call backend market symbols route
      const symbols = await endpoints.market.getSymbols();

      if (symbols && Array.isArray(symbols)) {
        setAvailableSymbols(symbols);
        return symbols;
      }
    } catch (err) {
      console.error("Failed to load markets:", err);
      // Fallback to hardcoded popular pairs
      const fallback = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
        "ADA/USDT", "DOGE/USDT", "MATIC/USDT", "DOT/USDT", "LTC/USDT"];
      setAvailableSymbols(fallback);
      return fallback;
    } finally {
      setIsLoadingSymbols(false);
    }
  }, [activeExchange]);

  // Fetch OHLCV data - uses backend /data/historical endpoint
  const fetchOHLCV = useCallback(async (symbol, timeframe, startDate, endDate, options = {}) => {
    const cacheKey = `${symbol}-${timeframe}-${startDate}-${endDate}`;

    // Check cache first
    if (ohlcCache.has(cacheKey) && !options.skipCache) {
      return ohlcCache.get(cacheKey);
    }

    setIsFetchingData(true);
    setFetchProgress(0);

    try {
      // Use new backend endpoint for historical data
      const response = await get('/data/historical', {
        params: {
          symbol,
          timeframe,
          start_date: startDate,
          end_date: endDate,
          exchange: activeExchange?.name || 'binance'
        }
      });

      if (!response?.data || !Array.isArray(response.data)) {
        throw new Error("Invalid data format received from server");
      }

      // Normalize to standardized OHLCV format
      const normalized = response.data.map(candle => ({
        timestamp: candle.timestamp || candle[0],
        datetime: new Date(candle.timestamp || candle[0]).toISOString(),
        open: parseFloat(candle.open || candle[1]),
        high: parseFloat(candle.high || candle[2]),
        low: parseFloat(candle.low || candle[3]),
        close: parseFloat(candle.close || candle[4]),
        volume: parseFloat(candle.volume || candle[5])
      }));

      setFetchProgress(100);

      // Cache the result
      const result = {
        symbol,
        timeframe,
        startDate,
        endDate,
        data: normalized,
        count: normalized.length,
        source: 'historical_api',
        fetchedAt: new Date().toISOString()
      };

      setOhlcCache(prev => new Map(prev).set(cacheKey, result));

      return result;
    } catch (err) {
      console.error("OHLCV fetch failed:", err);
      throw new Error(`Failed to fetch historical data: ${err.message}`);
    } finally {
      setIsFetchingData(false);
      setTimeout(() => setFetchProgress(0), 500);
    }
  }, [activeExchange, ohlcCache]);

  // Fetch historical data using pagination (fallback method)
  const fetchHistoricalPaginated = useCallback(async (symbol, timeframe, startDate, endDate) => {
    const cacheKey = `${symbol}-${timeframe}-${startDate}-${endDate}-paginated`;

    if (ohlcCache.has(cacheKey)) {
      return ohlcCache.get(cacheKey);
    }

    setIsFetchingData(true);
    setFetchProgress(0);

    try {
      const exchange = activeExchange?.name || 'binance';
      const allData = [];

      const since = new Date(startDate).getTime();
      const until = new Date(endDate).getTime();

      if (since >= until) {
        throw new Error("Start date must be before end date");
      }

      // Use CCXT proxy for paginated fetch
      let currentSince = since;
      const maxIterations = 100;
      let iterations = 0;

      while (currentSince < until && iterations < maxIterations) {
        // Use endpoints.market.getMarketData instead of missing CCXT route
        const data = await endpoints.market.getMarketData(symbol, timeframe, 1000);

        if (!data || data.length === 0) break;

        const normalized = data.map(candle => ({
          timestamp: candle.timestamp || candle[0],
          datetime: new Date(candle.timestamp || candle[0]).toISOString(),
          open: parseFloat(candle.open || candle[1]),
          high: parseFloat(candle.high || candle[2]),
          low: parseFloat(candle.low || candle[3]),
          close: parseFloat(candle.close || candle[4]),
          volume: parseFloat(candle.volume || candle[5])
        }));

        allData.push(...normalized);

        const progress = Math.min(100, (currentSince - since) / (until - since) * 100);
        setFetchProgress(progress);

        const lastCandle = normalized[normalized.length - 1];
        currentSince = lastCandle.timestamp + getTimeframeMs(timeframe);

        iterations++;

        if (iterations < maxIterations) {
          await new Promise(r => setTimeout(r, 100));
        }
      }

      const result = {
        symbol,
        timeframe,
        startDate,
        endDate,
        data: allData,
        count: allData.length,
        source: 'ccxt_paginated',
        fetchedAt: new Date().toISOString()
      };

      setOhlcCache(prev => new Map(prev).set(cacheKey, result));

      return result;
    } catch (err) {
      console.error("Paginated fetch failed:", err);
      throw err;
    } finally {
      setIsFetchingData(false);
      setFetchProgress(0);
    }
  }, [activeExchange, ohlcCache]);

  // Fetch orderbook imbalance from backend
  const fetchOrderbookImbalance = useCallback(async (symbol, depth = 20) => {
    try {
      // Use new backend endpoint
      const response = await get('/data/orderbook', {
        params: {
          symbol,
          depth,
          exchange: activeExchange?.name || 'binance'
        }
      });

      if (!response?.data) {
        return null;
      }

      const data = response.data;

      // Standardized orderbook imbalance format
      return {
        symbol: data.symbol || symbol,
        timestamp: data.timestamp || Date.now(),
        bidVolume: data.bid_volume || data.bidVolume || 0,
        askVolume: data.ask_volume || data.askVolume || 0,
        imbalance: data.imbalance || 0,
        spread: data.spread || 0,
        midPrice: data.mid_price || data.midPrice || 0
      };
    } catch (err) {
      console.error("Orderbook fetch failed:", err);
      return null;
    }
  }, [activeExchange]);

  // Connect to live WebSocket for real-time data
  const connectLiveData = useCallback((symbol, timeframe) => {
    if (pipelineMode !== 'live') return null;

    setIsLiveConnected(true);

    // WebSocket connection for live tick data
    const wsUrl = `${wsClient.url}/ws/market-data`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('Live data WebSocket connected');
      ws.send(JSON.stringify({
        action: 'subscribe',
        symbol,
        timeframe,
        exchange: activeExchange?.name || 'binance'
      }));
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'candle' || data.type === 'tick') {
        setLiveData({
          timestamp: data.timestamp,
          open: data.open,
          high: data.high,
          low: data.low,
          close: data.close,
          volume: data.volume,
          source: 'websocket'
        });
      }
    };

    ws.onerror = (err) => {
      console.error('Live WebSocket error:', err);
      setIsLiveConnected(false);
    };

    ws.onclose = () => {
      setIsLiveConnected(false);
    };

    return () => {
      ws.close();
      setIsLiveConnected(false);
    };
  }, [pipelineMode, activeExchange]);

  // Clear cache
  const clearCache = useCallback(() => {
    setOhlcCache(new Map());
  }, []);

  // Validate pipeline inputs
  const validateInputs = useCallback((symbol, timeframe, startDate, endDate) => {
    const errors = [];

    if (!symbol || !availableSymbols.includes(symbol)) {
      errors.push({ field: 'symbol', message: 'Invalid or unavailable symbol' });
    }

    const validTimeframes = ["1m", "5m", "15m", "1h", "4h", "1d"];
    if (!validTimeframes.includes(timeframe)) {
      errors.push({ field: 'timeframe', message: 'Invalid timeframe' });
    }

    const start = new Date(startDate);
    const end = new Date(endDate);

    if (isNaN(start.getTime()) || isNaN(end.getTime())) {
      errors.push({ field: 'dateRange', message: 'Invalid date format' });
    } else if (start >= end) {
      errors.push({ field: 'dateRange', message: 'Start date must be before end date' });
    }

    // Check if data is available for the range
    const maxHistory = getMaxHistoryForTimeframe(timeframe);
    const daysRequested = (end - start) / (1000 * 60 * 60 * 24);
    if (daysRequested > maxHistory) {
      errors.push({ field: 'dateRange', message: `Max ${maxHistory} days available for ${timeframe}` });
    }

    setValidationErrors(errors);
    return errors.length === 0;
  }, [availableSymbols]);

  const value = {
    // Mode
    mode: pipelineMode,
    setMode: setPipelineMode,

    // Exchange
    activeExchange,
    isLoadingExchange,
    availableTimeframes,

    // Symbols
    availableSymbols,
    symbolSearchQuery,
    setSymbolSearchQuery,
    isLoadingSymbols,
    loadMarkets,

    // Data fetching
    fetchOHLCV,
    fetchHistoricalPaginated,
    fetchOrderbookImbalance,
    connectLiveData,

    // Cache
    ohlcCache,
    isFetchingData,
    fetchProgress,
    clearCache,

    // Live data
    liveData,
    isLiveConnected,

    // Validation
    validateInputs,
    validationErrors,
    dataAvailability
  };

  return (
    <DataPipelineContext.Provider value={value}>
      {children}
    </DataPipelineContext.Provider>
  );
};

// ═══════════════════════════════════════════════════════════════════
//  INDICATOR ENGINE - Backend-Driven Computation
// ═══════════════════════════════════════════════════════════════════

const IndicatorEngineContext = createContext(null);

const useIndicatorEngine = () => {
  const context = useContext(IndicatorEngineContext);
  if (!context) throw new Error("useIndicatorEngine must be used within IndicatorEngineProvider");
  return context;
};

const IndicatorEngineProvider = ({ children }) => {
  // Cache for indicator results: key = "indicator_symbol_timeframe_params_hash"
  const [indicatorCache, setIndicatorCache] = useState(new Map());
  const [isComputing, setIsComputing] = useState(false);
  const [computationError, setComputationError] = useState(null);
  const [lastComputation, setLastComputation] = useState(null);

  // Generate cache key for indicator computation
  const generateCacheKey = useCallback((indicator, symbol, timeframe, params, dataLength) => {
    const paramsHash = JSON.stringify(params);
    return `${indicator}_${symbol}_${timeframe}_${paramsHash}_${dataLength}`;
  }, []);

  // Compute single indicator via backend
  const computeIndicator = useCallback(async (indicator, ohlcvData, params = {}, options = {}) => {
    if (!ohlcvData || ohlcvData.length === 0) {
      throw new Error("No OHLCV data provided for indicator computation");
    }

    const { symbol = 'unknown', timeframe = '1h' } = options;
    const cacheKey = generateCacheKey(indicator, symbol, timeframe, params, ohlcvData.length);

    // Check cache
    if (indicatorCache.has(cacheKey) && !options.skipCache) {
      return indicatorCache.get(cacheKey);
    }

    setIsComputing(true);
    setComputationError(null);

    try {
      // Standardize OHLCV input format
      const standardizedData = ohlcvData.map(candle => ({
        timestamp: candle.timestamp || candle[0],
        open: parseFloat(candle.open || candle[1]),
        high: parseFloat(candle.high || candle[2]),
        low: parseFloat(candle.low || candle[3]),
        close: parseFloat(candle.close || candle[4]),
        volume: parseFloat(candle.volume || candle[5] || 0)
      }));

      // Validate minimum data length
      const minLength = getMinDataLength(indicator, params);
      if (standardizedData.length < minLength) {
        throw new Error(`Not enough data for ${indicator.toUpperCase()} (need ${minLength} candles, got ${standardizedData.length})`);
      }

      // Call backend indicator engine
      const response = await post('/indicator/compute', {
        indicator: indicator.toLowerCase(),
        params,
        data: standardizedData,
        options: {
          symbol,
          timeframe,
          validate: true,
          handle_nan: true
        }
      });

      if (!response?.result) {
        throw new Error(`Invalid response from indicator engine for ${indicator}`);
      }

      // Standardize output format
      const result = {
        indicator: indicator.toLowerCase(),
        params,
        symbol,
        timeframe,
        data: response.result,
        metadata: {
          computedAt: new Date().toISOString(),
          dataPoints: standardizedData.length,
          outputKeys: Object.keys(response.result),
          ...response.metadata
        }
      };

      // Cache result
      setIndicatorCache(prev => new Map(prev).set(cacheKey, result));
      setLastComputation(result);

      return result;
    } catch (err) {
      // 🔴 STEP 11: Extract clear error message
      const errorMsg = extractErrorMessage(err, 'Indicator computation failed');
      setComputationError({
        indicator,
        message: errorMsg,
        params,
        timestamp: new Date().toISOString()
      });
      throw new Error(errorMsg);
    } finally {
      setIsComputing(false);
    }
  }, [indicatorCache, generateCacheKey]);

  // Compute multiple indicators in batch
  const computeIndicatorsBatch = useCallback(async (requests, ohlcvData) => {
    const results = {};

    for (const request of requests) {
      const { indicator, params, outputKey } = request;
      try {
        const result = await computeIndicator(indicator, ohlcvData, params);
        results[indicator] = outputKey ? result.data[outputKey] : result.data;
      } catch (err) {
        results[indicator] = { error: err.message };
      }
    }

    return results;
  }, [computeIndicator]);

  // Clear indicator cache
  const clearIndicatorCache = useCallback(() => {
    setIndicatorCache(new Map());
  }, []);

  // Get available outputs for multi-output indicators
  const getAvailableOutputs = useCallback((indicator) => {
    const multiOutputIndicators = {
      macd: ['macd', 'signal', 'histogram'],
      bollinger: ['upper', 'middle', 'lower'],
      bb: ['upper', 'middle', 'lower'],
      stochastic: ['k', 'd'],
      rsi: ['value'],
      ema: ['value'],
      sma: ['value'],
      atr: ['value'],
      obv: ['value'],
      vwap: ['value']
    };
    return multiOutputIndicators[indicator.toLowerCase()] || ['value'];
  }, []);

  // Validate indicator parameters
  const validateIndicatorParams = useCallback((indicator, params) => {
    const errors = [];

    const validations = {
      rsi: { period: { min: 2, max: 100, required: true } },
      macd: {
        fast: { min: 2, max: 50, required: true },
        slow: { min: 5, max: 200, required: true },
        signal: { min: 2, max: 50, required: true }
      },
      ema: { period: { min: 2, max: 200, required: true } },
      sma: { period: { min: 2, max: 200, required: true } },
      bollinger: {
        period: { min: 2, max: 100, required: true },
        stdDev: { min: 0.5, max: 5, required: true }
      },
      atr: { period: { min: 2, max: 100, required: true } }
    };

    const indicatorValidations = validations[indicator.toLowerCase()];
    if (indicatorValidations) {
      Object.entries(indicatorValidations).forEach(([param, rules]) => {
        const value = params[param];
        if (rules.required && (value === undefined || value === null)) {
          errors.push({ param, message: `${param} is required` });
        }
        if (value !== undefined && rules.min !== undefined && value < rules.min) {
          errors.push({ param, message: `${param} must be >= ${rules.min}` });
        }
        if (value !== undefined && rules.max !== undefined && value > rules.max) {
          errors.push({ param, message: `${param} must be <= ${rules.max}` });
        }
      });
    }

    return errors;
  }, []);

  const value = {
    computeIndicator,
    computeIndicatorsBatch,
    indicatorCache,
    clearIndicatorCache,
    isComputing,
    computationError,
    lastComputation,
    getAvailableOutputs,
    validateIndicatorParams
  };

  return (
    <IndicatorEngineContext.Provider value={value}>
      {children}
    </IndicatorEngineContext.Provider>
  );
};

// Helper: Get minimum data length required for indicator
const getMinDataLength = (indicator, params = {}) => {
  const minLengths = {
    rsi: (params.period || 14) + 1,
    macd: Math.max(params.fast || 12, params.slow || 26, params.signal || 9) + 1,
    ema: (params.period || 14) + 1,
    sma: (params.period || 14) + 1,
    bollinger: (params.period || 20) + 1,
    bb: (params.period || 20) + 1,
    atr: (params.period || 14) + 1,
    stochastic: (params.period || 14) + 1,
    obv: 2,
    vwap: 2
  };
  return minLengths[indicator.toLowerCase()] || 10;
};

// ═══════════════════════════════════════════════════════════════════
//  LOGIC ENGINE - Backend-Driven Signal Generation
// ═══════════════════════════════════════════════════════════════════

const LogicEngineContext = createContext(null);

const useLogicEngine = () => {
  const context = useContext(LogicEngineContext);
  if (!context) throw new Error("useLogicEngine must be used within LogicEngineProvider");
  return context;
};

const LogicEngineProvider = ({ children }) => {
  const [logicCache, setLogicCache] = useState(new Map());
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [evaluationError, setEvaluationError] = useState(null);
  const [lastSignal, setLastSignal] = useState(null);

  // Generate cache key for logic evaluation
  const generateLogicCacheKey = useCallback((conditions, indicatorsData) => {
    const conditionsHash = JSON.stringify(conditions);
    const dataHash = Object.keys(indicatorsData).sort().join(',');
    return `${conditionsHash}_${dataHash}`;
  }, []);

  // Evaluate conditions and generate signal
  const evaluateLogic = useCallback(async (conditions, indicatorsData, options = {}) => {
    if (!conditions || conditions.length === 0) {
      return { signal: 'NONE', confidence: 0, reason: 'No conditions defined' };
    }

    const cacheKey = generateLogicCacheKey(conditions, indicatorsData);

    // Check cache
    if (logicCache.has(cacheKey) && !options.skipCache) {
      return logicCache.get(cacheKey);
    }

    setIsEvaluating(true);
    setEvaluationError(null);

    try {
      // Validate conditions before sending
      const validationErrors = validateConditions(conditions, indicatorsData);
      if (validationErrors.length > 0) {
        throw new Error(`Validation failed: ${validationErrors.map(e => e.message).join(', ')}`);
      }

      // Call backend logic engine
      const response = await post('/logic/evaluate', {
        conditions,
        indicators: indicatorsData,
        options: {
          mode: options.mode || 'strict',
          minConfidence: options.minConfidence || 0.5,
          timestamp: new Date().toISOString()
        }
      });

      if (!response?.signal) {
        throw new Error('Invalid response from logic engine');
      }

      const result = {
        signal: response.signal, // 'BUY', 'SELL', 'NONE'
        confidence: response.confidence || 0,
        triggeredConditions: response.triggeredConditions || [],
        metadata: {
          evaluatedAt: new Date().toISOString(),
          conditionsCount: conditions.length,
          indicatorsUsed: Object.keys(indicatorsData),
          ...response.metadata
        }
      };

      // Cache result
      setLogicCache(prev => new Map(prev).set(cacheKey, result));
      setLastSignal(result);

      return result;
    } catch (err) {
      // 🔴 STEP 11: Extract clear error message
      const errorMsg = extractErrorMessage(err, 'Logic evaluation failed');
      setEvaluationError({
        message: errorMsg,
        conditions,
        timestamp: new Date().toISOString()
      });
      return { signal: 'NONE', confidence: 0, error: errorMsg };
    } finally {
      setIsEvaluating(false);
    }
  }, [logicCache, generateLogicCacheKey]);

  // Evaluate multiple logic nodes in batch
  const evaluateLogicBatch = useCallback(async (logicRequests) => {
    const results = {};

    for (const request of logicRequests) {
      const { nodeId, conditions, indicatorsData } = request;
      try {
        const result = await evaluateLogic(conditions, indicatorsData);
        results[nodeId] = result;
      } catch (err) {
        results[nodeId] = { signal: 'NONE', confidence: 0, error: err.message };
      }
    }

    return results;
  }, [evaluateLogic]);

  // Clear logic cache
  const clearLogicCache = useCallback(() => {
    setLogicCache(new Map());
  }, []);

  // Get available operators for UI
  const getAvailableOperators = useCallback(() => {
    return {
      comparison: [
        { value: '>', label: 'Greater Than', description: 'Value > Threshold' },
        { value: '<', label: 'Less Than', description: 'Value < Threshold' },
        { value: '>=', label: 'Greater or Equal', description: 'Value >= Threshold' },
        { value: '<=', label: 'Less or Equal', description: 'Value <= Threshold' },
        { value: '==', label: 'Equals', description: 'Value == Threshold' }
      ],
      crossover: [
        { value: 'crosses_above', label: 'Crosses Above', description: 'Series A crosses above Series B' },
        { value: 'crosses_below', label: 'Crosses Below', description: 'Series A crosses below Series B' }
      ],
      threshold: [
        { value: 'above_threshold', label: 'Above Threshold', description: 'Value > Constant' },
        { value: 'below_threshold', label: 'Below Threshold', description: 'Value < Constant' }
      ]
    };
  }, []);

  // Get condition template for UI
  const getConditionTemplate = useCallback((type = 'comparison') => {
    const templates = {
      comparison: {
        type: 'comparison',
        left: { source: 'indicator', name: '', key: 'value' },
        operator: '>',
        right: { source: 'constant', value: 0 }
      },
      crossover: {
        type: 'crossover',
        left: { source: 'indicator', name: '', key: 'value' },
        operator: 'crosses_above',
        right: { source: 'indicator', name: '', key: 'value' }
      },
      threshold: {
        type: 'threshold',
        left: { source: 'indicator', name: '', key: 'value' },
        operator: 'above_threshold',
        right: { source: 'constant', value: 70 }
      }
    };
    return templates[type] || templates.comparison;
  }, []);

  const value = {
    evaluateLogic,
    evaluateLogicBatch,
    logicCache,
    clearLogicCache,
    isEvaluating,
    evaluationError,
    lastSignal,
    getAvailableOperators,
    getConditionTemplate
  };

  return (
    <LogicEngineContext.Provider value={value}>
      {children}
    </LogicEngineContext.Provider>
  );
};

// ═══════════════════════════════════════════════════════════════════
//  STRATEGY ENGINE - Graph Parser & Execution Orchestration
// ═══════════════════════════════════════════════════════════════════

const StrategyEngineContext = createContext(null);

const useStrategyEngine = () => {
  const context = useContext(StrategyEngineContext);
  if (!context) throw new Error("useStrategyEngine must be used within StrategyEngineProvider");
  return context;
};

const StrategyEngineProvider = ({ children }) => {
  const [executionCache, setExecutionCache] = useState(new Map());
  const [isExecuting, setIsExecuting] = useState(false);
  const [executionError, setExecutionError] = useState(null);
  const [lastExecution, setLastExecution] = useState(null);

  // Generate unique execution ID using CSPRNG (replaces Math.random)
  const generateExecutionId = useCallback(() => {
    return `exec_${crypto.randomUUID()}`;
  }, []);

  // Parse node graph into execution plan (STEP 1)
  const parseGraphToExecutionPlan = useCallback((nodes, edges, options = {}) => {
    const errors = [];
    const warnings = [];

    // Validate graph structure
    if (!nodes || nodes.length === 0) {
      errors.push({ type: 'graph', message: 'No nodes in strategy graph' });
      return { valid: false, errors, plan: null };
    }

    // Find data source node (must have exactly one)
    const sourceNodes = nodes.filter(n => n.type === 'source');
    if (sourceNodes.length === 0) {
      errors.push({ type: 'source', message: 'No data source node found' });
    } else if (sourceNodes.length > 1) {
      warnings.push({ type: 'source', message: 'Multiple data sources found, using first one' });
    }

    const sourceNode = sourceNodes[0];
    const dataSource = sourceNode ? {
      nodeId: sourceNode.id,
      type: 'ccxt',
      symbol: sourceNode.data?.params?.symbol || 'BTC/USDT',
      timeframe: sourceNode.data?.params?.timeframe || '1h',
      exchange: sourceNode.data?.params?.exchange || 'binance',
      startDate: sourceNode.data?.params?.start_date,
      endDate: sourceNode.data?.params?.end_date
    } : null;

    // Build adjacency list for edge traversal
    const adjacency = {};
    edges.forEach(edge => {
      if (!adjacency[edge.source]) adjacency[edge.source] = [];
      adjacency[edge.source].push(edge.target);
    });

    // Collect indicator nodes
    const indicatorNodes = nodes.filter(n => n.type === 'indicator');
    const indicators = indicatorNodes.map(node => {
      const params = node.data?.params || {};
      return {
        nodeId: node.id,
        name: node.data?.label?.toLowerCase().replace(/\s+/g, '_'),
        params: {
          period: params.window || params.period || 14,
          ...params
        },
        outputKey: params.output || 'value',
        dependencies: adjacency[node.id] || []
      };
    });

    // Collect logic nodes
    const logicNodes = nodes.filter(n => n.type === 'logic');
    const logic = logicNodes.map(node => {
      const params = node.data?.params || {};
      return {
        nodeId: node.id,
        type: params.condition_type || 'comparison',
        condition: {
          left: {
            source: 'indicator',
            name: params.left_indicator,
            key: params.left_output || 'value'
          },
          operator: params.operator || '>',
          right: params.right_type === 'indicator' ? {
            source: 'indicator',
            name: params.right_indicator,
            key: params.right_output || 'value'
          } : {
            source: 'constant',
            value: parseFloat(params.right_constant) || 0
          }
        },
        signals: {
          onTrue: params.signal_on_true || 'BUY',
          onFalse: params.signal_on_false || 'NONE'
        },
        minConfidence: parseFloat(params.min_confidence) || 0.5,
        dependencies: adjacency[node.id] || []
      };
    });

    // Collect action/execution nodes
    const actionNodes = nodes.filter(n => n.type === 'action');
    const executionRules = actionNodes.map(node => {
      const params = node.data?.params || {};
      return {
        nodeId: node.id,
        action: node.data?.label?.toLowerCase().replace(/\s+/g, '_'),
        params: {
          sizePct: parseFloat(params.size_pct) || 10,
          orderType: params.order_type || 'MARKET',
          trailPct: parseFloat(params.trail_pct) || 2.0
        },
        dependencies: adjacency[node.id] || []
      };
    });

    // Validate all nodes are connected (have at least one edge or are source)
    const connectedNodeIds = new Set();
    edges.forEach(edge => {
      connectedNodeIds.add(edge.source);
      connectedNodeIds.add(edge.target);
    });

    const disconnectedNodes = nodes.filter(n =>
      n.type !== 'source' && !connectedNodeIds.has(n.id)
    );

    if (disconnectedNodes.length > 0) {
      errors.push({
        type: 'connectivity',
        message: `${disconnectedNodes.length} nodes are not connected`,
        nodes: disconnectedNodes.map(n => n.id)
      });
    }

    // Check for circular dependencies
    const visited = new Set();
    const recursionStack = new Set();

    const hasCycle = (nodeId) => {
      visited.add(nodeId);
      recursionStack.add(nodeId);

      const neighbors = adjacency[nodeId] || [];
      for (const neighbor of neighbors) {
        if (!visited.has(neighbor) && hasCycle(neighbor)) {
          return true;
        } else if (recursionStack.has(neighbor)) {
          return true;
        }
      }

      recursionStack.delete(nodeId);
      return false;
    };

    for (const node of nodes) {
      if (!visited.has(node.id)) {
        if (hasCycle(node.id)) {
          errors.push({ type: 'cycle', message: 'Circular dependency detected in strategy graph' });
          break;
        }
      }
    }

    // Check required params
    nodes.forEach(node => {
      const params = node.data?.params || {};
      if (node.type === 'indicator') {
        if (!params.window && !params.period) {
          warnings.push({ type: 'params', nodeId: node.id, message: `${node.data?.label} using default period` });
        }
      }
    });

    // Build execution plan (STEP 2)
    const executionPlan = {
      version: '1.0',
      executionId: generateExecutionId(),
      timestamp: new Date().toISOString(),
      metadata: {
        strategyName: options.strategyName || 'Unnamed Strategy',
        mode: options.mode || 'backtest',
        totalNodes: nodes.length,
        nodeTypes: nodes.reduce((acc, n) => {
          acc[n.type] = (acc[n.type] || 0) + 1;
          return acc;
        }, {})
      },
      pipeline: {
        dataSource,
        indicators,
        logic,
        executionRules
      },
      graph: {
        nodes: nodes.map(n => ({ id: n.id, type: n.type, label: n.data?.label })),
        edges: edges.map(e => ({ source: e.source, target: e.target }))
      }
    };

    return {
      valid: errors.length === 0,
      errors,
      warnings,
      plan: executionPlan
    };
  }, [generateExecutionId]);

  // Execute strategy via single API call (STEP 3)
  const executeStrategy = useCallback(async (executionPlan, options = {}) => {
    if (!executionPlan) {
      throw new Error('No execution plan provided');
    }

    const cacheKey = `exec_${JSON.stringify(executionPlan.pipeline)}`;

    // Check cache (STEP 8)
    if (executionCache.has(cacheKey) && !options.skipCache) {
      console.log('Using cached execution results');
      return executionCache.get(cacheKey);
    }

    setIsExecuting(true);
    setExecutionError(null);

    try {
      // Single API call to backend
      const response = await post('/strategy/execute', {
        plan: executionPlan,
        options: {
          mode: options.mode || 'backtest',
          generateCharts: options.generateCharts !== false,
          generateMetrics: options.generateMetrics !== false,
          ...options
        }
      });

      if (!response) {
        throw new Error('Empty response from strategy engine');
      }

      // Map backend output (STEP 4)
      const executionResult = {
        executionId: executionPlan.executionId,
        status: response.status || 'completed',
        signals: response.signals || [],
        charts: response.charts || {},
        metrics: response.metrics || {},
        trades: response.trades || [],
        performance: response.performance || {},
        metadata: {
          executedAt: new Date().toISOString(),
          backendVersion: response.version,
          ...response.metadata
        }
      };

      // Cache results
      setExecutionCache(prev => new Map(prev).set(cacheKey, executionResult));
      setLastExecution(executionResult);

      return executionResult;
    } catch (err) {
      // 🔴 STEP 11: Extract clear error message from backend response
      const errorMsg = extractErrorMessage(err, 'Strategy execution failed');
      const errorType = getErrorType(err);

      setExecutionError({
        type: errorType,
        message: errorMsg,
        executionId: executionPlan.executionId,
        timestamp: new Date().toISOString()
      });

      throw new Error(errorMsg);
    } finally {
      setIsExecuting(false);
    }
  }, [executionCache]);

  // Validate before execution (STEP 6)
  const validateStrategy = useCallback((nodes, edges) => {
    const { valid, errors, warnings } = parseGraphToExecutionPlan(nodes, edges);
    return { valid, errors, warnings };
  }, [parseGraphToExecutionPlan]);

  // Clear execution cache
  const clearExecutionCache = useCallback(() => {
    setExecutionCache(new Map());
  }, []);

  const value = {
    parseGraphToExecutionPlan,
    executeStrategy,
    validateStrategy,
    executionCache,
    clearExecutionCache,
    isExecuting,
    executionError,
    lastExecution,
    generateExecutionId
  };

  return (
    <StrategyEngineContext.Provider value={value}>
      {children}
    </StrategyEngineContext.Provider>
  );
};

// Helper: Validate conditions
const validateConditions = (conditions, indicatorsData) => {
  const errors = [];

  conditions.forEach((condition, index) => {
    // Check required fields
    if (!condition.type) {
      errors.push({ index, field: 'type', message: 'Condition type is required' });
    }
    if (!condition.operator) {
      errors.push({ index, field: 'operator', message: 'Operator is required' });
    }
    if (!condition.left) {
      errors.push({ index, field: 'left', message: 'Left operand is required' });
    }
    if (!condition.right) {
      errors.push({ index, field: 'right', message: 'Right operand is required' });
    }

    // Validate indicator references
    if (condition.left?.source === 'indicator' && condition.left?.name) {
      if (!indicatorsData[condition.left.name]) {
        errors.push({ index, field: 'left.name', message: `Indicator '${condition.left.name}' not found` });
      }
    }
    if (condition.right?.source === 'indicator' && condition.right?.name) {
      if (!indicatorsData[condition.right.name]) {
        errors.push({ index, field: 'right.name', message: `Indicator '${condition.right.name}' not found` });
      }
    }

    // Validate crossover has enough data points
    if (condition.type === 'crossover') {
      const leftData = condition.left?.source === 'indicator' ? indicatorsData[condition.left.name] : null;
      if (leftData && leftData.length < 2) {
        errors.push({ index, field: 'left', message: 'Crossover requires at least 2 data points' });
      }
    }
  });

  return errors;
};

// Helper functions
const getTimeframeMs = (tf) => {
  const multipliers = { 'm': 60 * 1000, 'h': 60 * 60 * 1000, 'd': 24 * 60 * 60 * 1000 };
  const unit = tf.slice(-1);
  const value = parseInt(tf);
  return value * (multipliers[unit] || multipliers['m']);
};

const getMaxHistoryForTimeframe = (tf) => {
  // Approximate max history available from exchanges
  const limits = { '1m': 7, '5m': 30, '15m': 90, '1h': 180, '4h': 365, '1d': 1000 };
  return limits[tf] || 30;
};

// ═══════════════════════════════════════════════════════════════════

const STRATEGY_TOOLBOX = [
  {
    g: "Data Sources", items: [
      { label: "CCXT Asset Feed", type: "source" },
      { label: "Orderbook Imbalance", type: "orderbook" },
      { label: "Live Ticker", type: "liveticker" }
    ]
  },
  {
    g: "Indicators", type: "indicator", items: [
      "SMA", "EMA", "WMA", "HMA", "RSI", "MACD", "ATR", "Bollinger Bands", "Stochastic", "CCI",
      "Williams %R", "OBV", "MFI", "ADX", "Supertrend", "TRIX", "Vortex", "Choppiness", "Awesome Oscillator",
      "Fisher Transform", "Z-Score", "Historical Volatility", "VWAP", "Momentum", "ROC", "Donchian",
      "Keltner Channels", "Ichimoku", "CMF", "PSAR", "Fibonacci", "Pivot Standard", "Pivot Camarilla",
    ]
  },
  { g: "ML Models", type: "mlmodel", items: ["XGBoost", "LightGBM", "RandomForest", "CatBoost", "LSTM", "GRU", "Transformer", "Autoencoder"] },
  { g: "Logic", type: "logic", items: ["Signal Logic", "And Gate", "Or Gate", "Condition Builder"] },
  { g: "Operators", type: "operator", items: ["Constant", "Compare", "Math", "Crosses"] },
  { g: "Execution", type: "action", items: ["Buy Market", "Sell Market", "Close Position", "Trailing Stop"] },
];

const ApiSyncIndicator = ({ color, text, active = true }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10, paddingTop: 6, borderTop: `1px dashed ${C.border}` }}>
    <div style={{ width: 6, height: 6, borderRadius: '50%', background: active ? color : C.t3, boxShadow: active ? `0 0 8px ${color}` : 'none', transition: 'all 0.3s' }} />
    <span style={{ fontSize: 8, color: active ? C.t2 : C.t4, letterSpacing: 1, fontFamily: "monospace", textTransform: "uppercase", fontWeight: 700 }}>{text}</span>
  </div>
);

// Premium Node Components with hover effects and glow
const PremiumNodeWrapper = ({ children, color, active = true }) => {
  const [isHovered, setIsHovered] = useState(false);
  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        minWidth: 150,
        background: C.bg3,
        border: `1.5px solid ${isHovered ? color : color + "90"}`,
        borderRadius: 8,
        color: C.t1,
        padding: "10px",
        boxShadow: isHovered ? `${C.glow[color === C.green ? "profit" : color === C.red ? "loss" : "accent"]}, 0 4px 12px rgba(0,0,0,0.3)` : `0 0 15px ${color}25`,
        fontFamily: "monospace",
        transition: "all 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
        transform: isHovered ? "scale(1.02)" : "scale(1)",
        cursor: "pointer"
      }}
    >
      {children}
      {active && (
        <div style={{
          position: "absolute",
          top: 6,
          right: 6,
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
          animation: "nodePulse 2s ease-in-out infinite",
          boxShadow: `0 0 8px ${color}`
        }}>
          <style>{`@keyframes nodePulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.6; transform: scale(0.8); } }`}</style>
        </div>
      )}
    </div>
  );
};

const SourceNode = ({ data }) => (
  <PremiumNodeWrapper color={C.t2}>
    <Handle type="source" position={Position.Right} style={{ background: C.t2, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
    <div style={{ color: C.t2, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
      <Radio size={10} />
      Data Ingestion
    </div>
    <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
    <ApiSyncIndicator color={C.accent} text="WebSocket Active" active={true} />
  </PremiumNodeWrapper>
);

const IndicatorNode = ({ data }) => {
  const outputKey = data?.params?.output;
  const isMultiOutput = data?.label === 'MACD' || data?.label === 'Bollinger Bands';

  return (
    <PremiumNodeWrapper color={C.accent}>
      <Handle type="target" position={Position.Left} style={{ background: C.accent, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <div style={{ color: C.accent, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        <Activity size={10} />
        Transformation
      </div>
      <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
      {isMultiOutput && outputKey && (
        <div style={{ fontSize: 9, color: C.t2, marginTop: 2, fontFamily: 'monospace' }}>
          output: {outputKey}
        </div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: C.accent, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
    </PremiumNodeWrapper>
  );
};

const MlModelNode = ({ data }) => (
  <PremiumNodeWrapper color={C.purple}>
    <Handle type="target" position={Position.Left} style={{ background: C.purple, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
    <div style={{ color: C.purple, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
      <Brain size={10} />
      Prediction
    </div>
    <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
    <Handle type="source" position={Position.Right} style={{ background: C.purple, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
    <ApiSyncIndicator color={C.purple} text="Model Synced" active={true} />
  </PremiumNodeWrapper>
);

const OperatorNode = ({ data }) => (
  <PremiumNodeWrapper color={C.gold}>
    <Handle type="target" position={Position.Left} style={{ background: C.gold, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
    <div style={{ color: C.gold, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
      <GitBranch size={10} />
      Logic Gate
    </div>
    <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
    <Handle type="source" position={Position.Right} style={{ background: C.gold, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
  </PremiumNodeWrapper>
);

const LogicNode = ({ data }) => {
  const lastSignal = data?.lastSignal;
  const signalColor = lastSignal === 'BUY' ? C.profit : lastSignal === 'SELL' ? C.loss : C.t2;
  const confidence = data?.confidence || 0;

  return (
    <PremiumNodeWrapper color={C.gold}>
      <Handle type="target" position={Position.Left} style={{ background: C.gold, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <div style={{ color: C.gold, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        <GitBranch size={10} />
        Signal Logic
      </div>
      <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
      {lastSignal && (
        <div style={{
          fontSize: 9,
          color: signalColor,
          marginTop: 2,
          fontFamily: 'monospace',
          fontWeight: 700,
          background: `${signalColor}20`,
          padding: '2px 6px',
          borderRadius: 3,
          display: 'inline-block'
        }}>
          {lastSignal} {confidence > 0 && `(${Math.round(confidence * 100)}%)`}
        </div>
      )}
      <Handle type="source" position={Position.Right} style={{ background: C.gold, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
    </PremiumNodeWrapper>
  );
};

const ActionNode = ({ data }) => {
  const isBuy = data.label.includes("Buy");
  const isSell = data.label.includes("Sell") || data.label.includes("Close") || data.label.includes("Stop");
  const color = isBuy ? C.profit : isSell ? C.loss : C.warning;
  const glowKey = isBuy ? "profit" : isSell ? "loss" : "warning";

  return (
    <PremiumNodeWrapper color={color}>
      <Handle type="target" position={Position.Left} style={{ background: color, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <div style={{ color: color, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        <Zap size={10} />
        Execution Route
      </div>
      <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
      <ApiSyncIndicator color={color} text="Router Armed" active={true} />
    </PremiumNodeWrapper>
  );
};

// Orderbook Imbalance Node - Live data only
const OrderbookImbalanceNode = ({ data }) => {
  const imbalance = data?.params?.imbalance || 0;
  const isBullish = imbalance > 0;
  const color = isBullish ? C.profit : C.loss;

  return (
    <PremiumNodeWrapper color={C.purple}>
      <Handle type="target" position={Position.Left} style={{ background: C.purple, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <div style={{ color: C.purple, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        <BookOpen size={10} />
        Orderbook Flow
      </div>
      <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
      <div style={{
        fontSize: 10,
        color: isBullish ? C.profit : C.loss,
        marginTop: 4,
        fontFamily: "monospace"
      }}>
        Imbalance: {imbalance > 0 ? "+" : ""}{imbalance.toFixed(3)}
      </div>
      <ApiSyncIndicator color={C.purple} text={isBullish ? "Bid Dominant" : "Ask Dominant"} active={true} />
      <Handle type="source" position={Position.Right} style={{ background: C.purple, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
    </PremiumNodeWrapper>
  );
};

// Live Ticker Node - Real-time price feed
const LiveTickerNode = ({ data }) => {
  const isLive = data?.params?.mode === 'live';

  return (
    <PremiumNodeWrapper color={isLive ? C.accent : C.t3} active={isLive}>
      <Handle type="source" position={Position.Right} style={{ background: isLive ? C.accent : C.t3, border: `1px solid ${C.bg1}`, width: 10, height: 10 }} />
      <div style={{ color: isLive ? C.accent : C.t3, fontSize: 8, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
        {isLive ? <Wifi size={10} /> : <WifiOff size={10} />}
        Live Stream
      </div>
      <div style={{ fontWeight: 900, fontSize: 13 }}>{data.label}</div>
      <div style={{ fontSize: 9, color: C.t2, marginTop: 4 }}>
        {isLive ? "Real-time ticks" : "Backtest mode - disabled"}
      </div>
      <ApiSyncIndicator color={isLive ? C.accent : C.t3} text={isLive ? "Connected" : "Standby"} active={isLive} />
    </PremiumNodeWrapper>
  );
};

function StrategyBuilderInner({ onBack, strategy, initialStrategy, onBacktest }) {
  // Data Pipeline integration
  const {
    mode: pipelineMode,
    setMode: setPipelineMode,
    activeExchange,
    isLoadingExchange,
    availableTimeframes,
    availableSymbols,
    isLoadingSymbols,
    loadMarkets,
    fetchOHLCV,
    fetchOrderbookImbalance,
    connectLiveData,
    isFetchingData,
    fetchProgress,
    isLiveConnected,
    validateInputs,
    validationErrors
  } = useDataPipeline();

  // Indicator Engine integration
  const {
    computeIndicator,
    computeIndicatorsBatch,
    indicatorCache,
    clearIndicatorCache,
    isComputing,
    computationError,
    lastComputation,
    getAvailableOutputs,
    validateIndicatorParams
  } = useIndicatorEngine();

  // Logic Engine integration
  const {
    evaluateLogic,
    evaluateLogicBatch,
    logicCache,
    clearLogicCache,
    isEvaluating,
    evaluationError,
    lastSignal,
    getAvailableOperators,
    getConditionTemplate
  } = useLogicEngine();

  // Strategy Engine integration
  const {
    parseGraphToExecutionPlan,
    executeStrategy,
    validateStrategy,
    executionCache,
    clearExecutionCache,
    isExecuting,
    executionError,
    lastExecution,
    generateExecutionId
  } = useStrategyEngine();

  // Mode toggle state
  const [builderMode, setBuilderMode] = useState('backtest'); // 'backtest' | 'live'

  // Update pipeline mode when builder mode changes
  useEffect(() => {
    setPipelineMode(builderMode);
  }, [builderMode, setPipelineMode]);

  // Load symbols when exchange is ready
  useEffect(() => {
    if (activeExchange && !isLoadingExchange) {
      loadMarkets();
    }
  }, [activeExchange, isLoadingExchange, loadMarkets]);

  const strategyNodeTypes = useMemo(() => ({
    source: SourceNode,
    indicator: IndicatorNode,
    mlmodel: MlModelNode,
    operator: OperatorNode,
    logic: LogicNode,
    action: ActionNode,
    orderbook: OrderbookImbalanceNode,
    liveticker: LiveTickerNode,
  }), []);
  const [period, setPeriod] = useState(14);
  const [strategyName, setStrategyName] = useState("Untitled Strategy");
  const [isSavingStrategy, setIsSavingStrategy] = useState(false);
  const [saveState, setSaveState] = useState("");
  // Error handling state
  const [pipelineError, setPipelineError] = useState(null);
  const [selectedDeployExchange, setSelectedDeployExchange] = useState("binance");

  const [ccxtMarkets, setCcxtMarkets] = useState(["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]); // Fallback
  const reactFlowWrapper = useRef(null);
  const nodeSeq = useRef(4);
  const loadedStrategy = initialStrategy || strategy || null;
  const [strategyIdState, setStrategyIdState] = useState(loadedStrategy?.id || null);
  // Helper to get default date range
  const getDefaultDateRange = () => {
    const end = new Date();
    const start = new Date();
    start.setDate(start.getDate() - 30); // Default 30 days back
    return {
      start_date: start.toISOString().split('T')[0],
      end_date: end.toISOString().split('T')[0]
    };
  };

  const defaultDates = getDefaultDateRange();

  const initialNodes = [
    { id: "n-1", type: "source", position: { x: 50, y: 250 }, data: { label: "CCXT Asset Feed", params: { symbol: "BTC/USDT", timeframe: "15m", start_date: defaultDates.start_date, end_date: defaultDates.end_date } } },
    { id: "n-2", type: "indicator", position: { x: 300, y: 150 }, data: { label: "RSI", params: { window: 14 } } },
    { id: "n-3", type: "mlmodel", position: { x: 550, y: 150 }, data: { label: "XGBoost", params: { confidence: 0.8 } } },
    { id: "n-4", type: "operator", position: { x: 300, y: 350 }, data: { label: "Math", params: { operation: "+" } } },
    { id: "n-5", type: "operator", position: { x: 800, y: 250 }, data: { label: "Logic", params: { gate: "AND" } } },
    { id: "n-6", type: "action", position: { x: 1050, y: 150 }, data: { label: "Buy Market", params: { size_pct: 15, order_type: "MARKET" } } },
    { id: "n-7", type: "action", position: { x: 1050, y: 350 }, data: { label: "Sell Market", params: { size_pct: 15, order_type: "MARKET" } } },
  ];
  const initialEdges = [
    { id: "e-1-2", source: "n-1", target: "n-2", markerEnd: { type: MarkerType.ArrowClosed, color: C.cyan }, style: { stroke: C.cyan, strokeWidth: 2 } },
    { id: "e-1-4", source: "n-1", target: "n-4", markerEnd: { type: MarkerType.ArrowClosed, color: C.t2 }, style: { stroke: C.t2, strokeWidth: 2 } },
    { id: "e-2-3", source: "n-2", target: "n-3", markerEnd: { type: MarkerType.ArrowClosed, color: C.purple }, style: { stroke: C.purple, strokeWidth: 2 } },
    { id: "e-3-5", source: "n-3", target: "n-5", markerEnd: { type: MarkerType.ArrowClosed, color: C.gold }, style: { stroke: C.gold, strokeWidth: 2 } },
    { id: "e-4-5", source: "n-4", target: "n-5", markerEnd: { type: MarkerType.ArrowClosed, color: C.gold }, style: { stroke: C.gold, strokeWidth: 2 } },
    { id: "e-5-6", source: "n-5", target: "n-6", markerEnd: { type: MarkerType.ArrowClosed, color: C.green }, style: { stroke: C.green, strokeWidth: 2 } },
    { id: "e-5-7", source: "n-5", target: "n-7", markerEnd: { type: MarkerType.ArrowClosed, color: C.red }, style: { stroke: C.red, strokeWidth: 2 } },
  ];
  const [nodes, setNodes] = useState(initialNodes);
  const [edges, setEdges] = useState(initialEdges);
  const [selectedNodeId, setSelectedNodeId] = useState("n-1");
  const [reactFlowInstance, setReactFlowInstance] = useState(null);

  // Connect live data when in live mode (must be after nodes declaration)
  useEffect(() => {
    if (builderMode === 'live') {
      const sourceNode = nodes.find(n => n.type === 'source' && n.data?.label === 'CCXT Asset Feed');
      if (sourceNode) {
        const { symbol, timeframe } = sourceNode.data?.params || {};
        if (symbol && timeframe) {
          const disconnect = connectLiveData(symbol, timeframe);
          // Ensure cleanup is always a function
          return typeof disconnect === 'function' ? disconnect : undefined;
        }
      }
    }
    // Return undefined explicitly when no cleanup needed
    return undefined;
  }, [builderMode, nodes, connectLiveData]);
  const [pastStates, setPastStates] = useState([]);
  const [futureStates, setFutureStates] = useState([]);

  const selectedNode = nodes.find((n) => n.id === selectedNodeId) || null;
  const onNodesChange = useCallback((changes) => {
    const hasStructuralChange = changes.some(change =>
      change.type === 'add' || change.type === 'remove' || change.type === 'replace'
    );

    if (hasStructuralChange) {
      setPastStates(prev => [...prev, { nodes, edges }]);
      setFutureStates([]);
    }

    setNodes((prev) => applyNodeChanges(changes, prev));
  }, [nodes, edges]);
  const onEdgesChange = useCallback((changes) => {
    const hasStructuralChange = changes.some(change =>
      change.type === 'add' || change.type === 'remove' || change.type === 'replace'
    );

    if (hasStructuralChange) {
      setPastStates(prev => [...prev, { nodes, edges }]);
      setFutureStates([]);
    }

    setEdges((prev) => applyEdgeChanges(changes, prev));
  }, [nodes, edges]);

  const onConnect = useCallback(
    (params) => {
      setPastStates(prev => [...prev, { nodes, edges }]);
      setFutureStates([]);
      setEdges((eds) =>
        addEdge(
          {
            ...params,
            markerEnd: { type: MarkerType.ArrowClosed, color: C.cyan },
            style: { stroke: C.cyan, strokeWidth: 1.6 },
          },
          eds
        )
      );
    },
    [nodes, edges]
  );

  const handleUndo = useCallback(() => {
    if (pastStates.length === 0) return;

    const previousState = pastStates[pastStates.length - 1];
    setPastStates(prev => prev.slice(0, -1));
    setFutureStates(prev => [...prev, { nodes, edges }]);
    setNodes(previousState.nodes);
    setEdges(previousState.edges);
  }, [pastStates, nodes, edges]);

  const handleDeleteNode = useCallback(() => {
    if (!selectedNodeId) return;

    setPastStates(prev => [...prev, { nodes, edges }]);
    setFutureStates([]);

    setNodes(prev => prev.filter(node => node.id !== selectedNodeId));
    setEdges(prev => prev.filter(edge =>
      edge.source !== selectedNodeId && edge.target !== selectedNodeId
    ));
    setSelectedNodeId(null);
  }, [selectedNodeId, nodes, edges]);

  const getNodeParamSchema = (label = "", type = "") => {
    // Data Source nodes - CCXT Asset Feed (exchange auto-fetched from vault)
    if (type === "source" && label === "CCXT Asset Feed") {
      const today = new Date().toISOString().split('T')[0];
      const thirtyDaysAgo = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
      return [
        { key: "symbol", label: "Asset Symbol", type: "datalist", defaultValue: "BTC/USDT" },
        { key: "timeframe", label: "Timeframe", type: "select", options: ["1m", "5m", "15m", "1h", "4h", "1d"], defaultValue: "15m" },
        { key: "start_date", label: "Start Date", type: "date", defaultValue: thirtyDaysAgo },
        { key: "end_date", label: "End Date", type: "date", defaultValue: today }
      ];
    }

    // Orderbook Imbalance node - Live execution only
    if (type === "orderbook" || label === "Orderbook Imbalance") {
      return [
        { key: "depth", label: "Orderbook Depth", type: "select", options: ["10", "20", "50", "100"], defaultValue: "20" },
        { key: "threshold", label: "Signal Threshold", type: "number", defaultValue: 0.2 },
        { key: "mode", label: "Mode", type: "select", options: ["live", "backtest_placeholder"], defaultValue: "live" }
      ];
    }

    // Live Ticker node - Real-time price feed
    if (type === "liveticker" || label === "Live Ticker") {
      return [
        { key: "mode", label: "Mode", type: "select", options: ["live", "disabled"], defaultValue: "live" },
        { key: "update_interval", label: "Update Interval (ms)", type: "select", options: ["100", "250", "500", "1000"], defaultValue: "250" }
      ];
    }

    if (type === "action") {
      if (label === "Trailing Stop") return [{ key: "trail_pct", label: "Trail Distance %", type: "number", defaultValue: 2.0 }];
      return [
        { key: "size_pct", label: "Capital Allocation %", type: "number", defaultValue: 10 },
        { key: "order_type", label: "Order Type", type: "select", options: ["MARKET", "LIMIT"], defaultValue: "MARKET" }
      ];
    }
    if (type === "operator") {
      if (label === "Constant") return [{ key: "value", label: "Threshold Value", type: "number", defaultValue: 0 }];
      if (label === "Compare") return [{ key: "condition", label: "Condition", type: "select", options: [">", "<", "==", ">=", "<="], defaultValue: ">" }];
      if (label === "Math") return [{ key: "operation", label: "Operation", type: "select", options: ["+", "-", "*", "/"], defaultValue: "+" }];
      if (label === "Crosses") return [{ key: "direction", label: "Direction", type: "select", options: ["Crosses Above", "Crosses Below"], defaultValue: "Crosses Above" }];
    }

    // Logic nodes - Signal generation with condition builder
    if (type === "logic") {
      if (label === "Signal Logic" || label === "Condition Builder") {
        return [
          { key: "condition_type", label: "Condition Type", type: "select", options: ["comparison", "crossover", "threshold"], defaultValue: "comparison" },
          { key: "left_indicator", label: "Left Indicator", type: "select", options: ["RSI", "MACD", "SMA", "EMA", "Price", "Volume"], defaultValue: "RSI" },
          { key: "left_output", label: "Left Output", type: "select", options: ["value", "macd", "signal", "histogram", "upper", "middle", "lower"], defaultValue: "value" },
          { key: "operator", label: "Operator", type: "select", options: [">", "<", ">=", "<=", "==", "crosses_above", "crosses_below"], defaultValue: ">" },
          { key: "right_type", label: "Right Side", type: "select", options: ["indicator", "constant"], defaultValue: "constant" },
          { key: "right_indicator", label: "Right Indicator", type: "select", options: ["RSI", "MACD", "SMA", "EMA", "Price", "Volume"], defaultValue: "SMA" },
          { key: "right_output", label: "Right Output", type: "select", options: ["value", "macd", "signal", "histogram", "upper", "middle", "lower"], defaultValue: "value" },
          { key: "right_constant", label: "Constant Value", type: "number", defaultValue: 70 },
          { key: "signal_on_true", label: "Signal When True", type: "select", options: ["BUY", "SELL", "NONE"], defaultValue: "BUY" },
          { key: "signal_on_false", label: "Signal When False", type: "select", options: ["BUY", "SELL", "NONE"], defaultValue: "NONE" },
          { key: "min_confidence", label: "Min Confidence %", type: "number", defaultValue: 0.5 }
        ];
      }
      if (label === "And Gate") return [
        { key: "gate_type", label: "Gate Type", type: "select", options: ["AND", "OR"], defaultValue: "AND" },
        { key: "input_count", label: "Number of Inputs", type: "select", options: ["2", "3", "4"], defaultValue: "2" }
      ];
      if (label === "Or Gate") return [
        { key: "gate_type", label: "Gate Type", type: "select", options: ["AND", "OR"], defaultValue: "OR" },
        { key: "input_count", label: "Number of Inputs", type: "select", options: ["2", "3", "4"], defaultValue: "2" }
      ];
    }
    if (type === "mlmodel") {
      if (label === "LSTM" || label === "GRU") return [
        { key: "confidence", label: "Min Confidence", type: "number", defaultValue: 0.80 },
        { key: "epochs", label: "Epochs (Pre-trained)", type: "number", defaultValue: 100 }
      ];
      return [
        { key: "confidence", label: "Min Confidence Score", type: "number", defaultValue: 0.75 },
        { key: "estimators", label: "N_Estimators", type: "number", defaultValue: 100 }
      ];
    }
    // Indicators
    if (label === "MACD") return [
      { key: "fast", label: "Fast Period", type: "number", defaultValue: 12 },
      { key: "slow", label: "Slow Period", type: "number", defaultValue: 26 },
      { key: "signal", label: "Signal Period", type: "number", defaultValue: 9 },
      { key: "output", label: "Output Series", type: "select", options: ["macd", "signal", "histogram"], defaultValue: "macd" }
    ];
    if (label === "Bollinger Bands") return [
      { key: "window", label: "Window", type: "number", defaultValue: 20 },
      { key: "multiplier", label: "Std Deviations", type: "number", defaultValue: 2.0 },
      { key: "output", label: "Output Series", type: "select", options: ["upper", "middle", "lower"], defaultValue: "middle" }
    ];
    if (label === "RSI" || label === "SMA" || label === "EMA") return [
      { key: "window", label: "Lookback Window", type: "number", defaultValue: 14 },
      { key: "source", label: "Source Price", type: "select", options: ["close", "open", "high", "low"], defaultValue: "close" }
    ];
    return [{ key: "window", label: "Lookback Window", type: "number", defaultValue: 14 }];
  };

  const updateSelectedNodeParam = (key, value) => {
    if (!selectedNodeId) return;
    setNodes((prev) =>
      Array.isArray(prev) ? prev.map((node) =>
        node.id !== selectedNodeId
          ? node
          : {
            ...node,
            data: {
              ...(node.data || {}),
              params: {
                ...(node.data?.params || {}),
                [key]: value,
              },
            },
          }
      ) : prev
    );
  };

  const renderDynamicField = (field, selectedNodeLocal) => {
    const value = selectedNodeLocal?.data?.params?.[field.key] ?? field.defaultValue;
    const commonStyle = { background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace", width: "100%", outline: "none" };

    if (field.type === "select") {
      return (
        <div key={field.key}>
          <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", display: "block", marginBottom: 6 }}>{field.label}</label>
          <select value={String(value)} onChange={(e) => updateSelectedNodeParam(field.key, e.target.value)} style={commonStyle}>
            {field.options.map((opt) => <option key={opt} value={opt} style={{ background: C.bg3, color: C.t1 }}>{opt}</option>)}
          </select>
        </div>
      );
    }

    if (field.type === "datalist") {
      // Use availableSymbols from data pipeline
      const symbolsToUse = availableSymbols.length > 0 ? availableSymbols : ccxtMarkets;
      return (
        <div key={field.key}>
          <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", display: "block", marginBottom: 6 }}>
            {field.label}
            {isLoadingSymbols && <Loader2 size={10} style={{ marginLeft: 6, animation: "spin 1s linear infinite" }} />}
          </label>
          <input
            list="ccxt-symbols"
            value={String(value)}
            onChange={(e) => updateSelectedNodeParam(field.key, e.target.value.toUpperCase())}
            placeholder={isLoadingSymbols ? "Loading markets..." : "Search asset..."}
            style={{ ...commonStyle, opacity: isLoadingSymbols ? 0.6 : 1 }}
            disabled={isLoadingSymbols}
          />
          <datalist id="ccxt-symbols">
            {symbolsToUse.slice(0, 100).map(s => <option key={s} value={s} />)}
          </datalist>
          {!isLoadingSymbols && availableSymbols.length > 100 && (
            <div style={{ fontSize: 8, color: C.t3, marginTop: 2 }}>
              Showing top 100 of {availableSymbols.length} markets. Type to search.
            </div>
          )}
        </div>
      );
    }

    if (field.type === "date") {
      return (
        <div key={field.key}>
          <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", display: "block", marginBottom: 6 }}>{field.label}</label>
          <input
            type="date"
            value={String(value)}
            onChange={(e) => updateSelectedNodeParam(field.key, e.target.value)}
            style={commonStyle}
          />
        </div>
      );
    }

    return (
      <Inp
        key={field.key}
        lbl={field.label}
        ph={String(field.defaultValue)}
        val={String(value)}
        onChange={(e) => updateSelectedNodeParam(field.key, field.type === "number" ? Number(e.target.value) : e.target.value)}
        type={field.type === "number" ? "number" : "text"}
      />
    );
  };

  useEffect(() => {
    if (!loadedStrategy) return;
    setStrategyName(loadedStrategy.name || "Untitled Strategy");
    if (Array.isArray(loadedStrategy.nodes) && loadedStrategy.nodes.length) {
      setNodes(loadedStrategy.nodes);
      const lastNum = loadedStrategy.nodes.reduce((m, n) => {
        const num = Number(String(n.id || "").replace(/[^\d]/g, ""));
        return Number.isFinite(num) ? Math.max(m, num) : m;
      }, 0);
      nodeSeq.current = Math.max(lastNum + 1, 1);
    }
    if (Array.isArray(loadedStrategy.edges)) setEdges(loadedStrategy.edges);
  }, [loadedStrategy]);

  useEffect(() => {
    const controller = new AbortController();
    const fetchMarkets = async () => {
      try {
        // Attempt to fetch from backend
        const data = await api.market.getSymbols();
        if (Array.isArray(data) && data.length > 0) {
          setCcxtMarkets(data);
          window.globalCcxtMarkets = data; // Make available to renderDynamicField
        }
      } catch (err) {
        // Silent fail - use fallback list
      }
    };
    fetchMarkets();
    return () => controller.abort();
  }, []);

  const handleDeployLive = async () => {
    if (isSavingStrategy) return;

    const isValid = await handleValidateStrategy();
    if (!isValid) return;

    // Validate before deploying
    const sourceNode = nodes.find(n => n.type === 'source' && n.data?.label === 'CCXT Asset Feed');
    if (sourceNode) {
      const { symbol, timeframe, start_date, end_date } = sourceNode.data?.params || {};
      if (!validateInputs(symbol, timeframe, start_date, end_date)) {
        console.warn("Validation failed for deployment:", validationErrors);
        return;
      }
    }

    setIsSavingStrategy(true);
    setSaveState("");
    try {
      // Must be saved first
      let currentId = strategyIdState || loadedStrategy?.id;
      if (!currentId) {
        console.error("Cannot deploy an unsaved strategy.");
        setSaveState("error");
        setTimeout(() => setSaveState(""), 2000);
        return;
      }

      // Hit the live deployment execution endpoint
      await endpoints.strategies.deploy(currentId, { exchange_id: selectedDeployExchange || "binance" });
      setSaveState("deployed");
      setTimeout(() => setSaveState(""), 2000);
    } catch (err) {
      console.error("Execution engine deployment failed:", err);
      setSaveState("error");
      setTimeout(() => setSaveState(""), 2000);
    } finally {
      setIsSavingStrategy(false);
    }
  };

  const handleSaveStrategy = async () => {
    if (isSavingStrategy) return;
    setIsSavingStrategy(true);
    setSaveState("");
    try {
      const isValid = await handleValidateStrategy();
      if (!isValid) {
        setIsSavingStrategy(false);
        return;
      }

      const { nodes: serNodes, edges: serEdges } = serializeReactFlowToDAG(nodes, edges);
      const payload = {
        name: strategyName,
        nodes: serNodes,
        edges: serEdges,
        buy_logic: { operator: "AND", conditions: [] },
        sell_logic: { operator: "AND", conditions: [] },
        risk: { position_size_pct: 0.1, stop_loss_pct: 0.05, take_profit_pct: 0.1 },
        symbol: "BTCUSDT",
        timeframe: "1h"
      };

      let currentId = strategyIdState || loadedStrategy?.id;
      if (currentId) {
        await endpoints.strategies.update(currentId, payload);
      } else {
        const res = await endpoints.strategies.create(payload);
        if (res?.strategy_id) {
          setStrategyIdState(res.strategy_id);
        }
      }
      setSaveState("saved");
      setTimeout(() => setSaveState(""), 1800);
    } catch (err) {
      console.error("Strategy save failed:", err);
      setSaveState("error");
      setTimeout(() => setSaveState(""), 2200);
    } finally {
      setIsSavingStrategy(false);
    }
  };

  const handleValidateStrategy = async () => {
    try {
      const { nodes: serNodes, edges: serEdges } = serializeReactFlowToDAG(nodes, edges);
      const payload = { dag: { nodes: serNodes, edges: serEdges } };
      console.log("Sending to POST /api/strategies/validate:", payload);
      const res = await endpoints.strategies.validate(payload);
      console.log("Validation Results:", res);
      if (res.errors && res.errors.length > 0) {
        setPipelineError({
          type: 'validation',
          message: `Backend validation failed: ${res.errors.join(', ')}`,
          details: res.errors
        });
        return false;
      }
      return true;
    } catch (err) {
      console.error("Backend validation endpoint failed", err);
      setPipelineError({
        type: 'validation',
        message: `Backend validation error: ${err.message}`,
        details: []
      });
      return false;
    }
  };

  const handleBacktest = async () => {
    if (!onBacktest) return;

    setPipelineError(null);

    // STEP 6: Validate strategy before execution
    const { valid: graphValid, errors: graphErrors, warnings: graphWarnings } = validateStrategy(nodes, edges);

    if (!graphValid) {
      console.warn("Strategy validation failed:", graphErrors);
      setPipelineError({
        type: 'validation',
        message: `Strategy validation failed: ${graphErrors.map(e => e.message).join(', ')}`,
        details: graphErrors
      });
      return;
    }

    if (graphWarnings.length > 0) {
      console.log("Strategy warnings:", graphWarnings);
    }

    // Validate pipeline data source
    const sourceNode = nodes.find(n => n.type === 'source' && n.data?.label === 'CCXT Asset Feed');
    if (!sourceNode) {
      setPipelineError({
        type: 'validation',
        message: 'No CCXT Asset Feed node found. Add a data source to run backtest.'
      });
      return;
    }

    const { symbol, timeframe, start_date, end_date } = sourceNode.data?.params || {};
    const isDataValid = validateInputs(symbol, timeframe, start_date, end_date);

    if (!isDataValid) {
      console.warn("Data validation failed:", validationErrors);
      setPipelineError({
        type: 'validation',
        message: 'Please fix data validation errors before running backtest',
        details: validationErrors
      });
      return;
    }

    try {
      // STEP 1 & 2: Parse graph into execution plan
      const { valid, errors, warnings, plan: executionPlan } = parseGraphToExecutionPlan(nodes, edges, {
        strategyName,
        mode: 'backtest'
      });

      if (!valid) {
        throw new Error(`Execution plan validation failed: ${errors.map(e => e.message).join(', ')}`);
      }

      console.log('Execution plan generated:', executionPlan.executionId);
      console.log(`Pipeline: ${executionPlan.pipeline.indicators.length} indicators, ${executionPlan.pipeline.logic.length} logic nodes, ${executionPlan.pipeline.executionRules.length} actions`);

      // Pre-fetch OHLCV data (still needed for the backend)
      const ohlcvData = await fetchOHLCV(symbol, timeframe, start_date, end_date);
      console.log(`Loaded ${ohlcvData.count} candles for backtest`);

      // Add OHLCV data to execution plan
      executionPlan.pipeline.dataSource.ohlcvData = ohlcvData.data;

      // STEP 3: Single API call to execute strategy
      const executionResult = await executeStrategy(executionPlan, {
        mode: 'backtest',
        generateCharts: true,
        generateMetrics: true
      });

      console.log('Strategy execution completed:', executionResult.executionId);

      // Update node data with results from backend (STEP 4)
      if (executionResult.signals) {
        executionResult.signals.forEach(signal => {
          if (signal.nodeId) {
            setNodes(prev => prev.map(n =>
              n.id === signal.nodeId
                ? { ...n, data: { ...n.data, lastSignal: signal.signal, confidence: signal.confidence } }
                : n
            ));
          }
        });
      }

      // Pass execution result to backtest view
      onBacktest({
        id: loadedStrategy?.id,
        name: strategyName,
        nodes,
        edges,
        executionPlan,
        executionResult, // Full backend response with charts, metrics, trades
        dataSource: {
          symbol,
          timeframe,
          start_date,
          end_date,
          exchange: activeExchange?.name || 'binance'
        },
        mode: 'backtest'
      });
    } catch (err) {
      console.error("Strategy execution failed:", err);

      // STEP 7: Error handling with categorization
      const errorType = err.message?.includes('validation') ? 'validation' :
        err.message?.includes('not found') ? 'missing_data' :
          err.message?.includes('connect') ? 'backend_failure' : 'execution';

      setPipelineError({
        type: errorType,
        message: err.message || 'Strategy execution failed',
        details: err
      });
    }
  };

  const onDragStartToolbox = (e, payload) => {
    e.dataTransfer.setData("application/reactflow", JSON.stringify(payload));
    e.dataTransfer.effectAllowed = "move";
  };

  const onDragOverCanvas = useCallback((e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDropCanvas = useCallback(
    (e) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData("application/reactflow");
      if (!raw || !reactFlowWrapper.current) return;
      const { nodeType, label } = JSON.parse(raw);
      const position = reactFlowInstance?.screenToFlowPosition
        ? reactFlowInstance.screenToFlowPosition({ x: e.clientX, y: e.clientY })
        : { x: 100, y: 100 };
      const nextId = `n-${nodeSeq.current++}`;
      const schema = getNodeParamSchema(label, nodeType);
      const params = schema.reduce((acc, field) => ({ ...acc, [field.key]: field.defaultValue }), {});
      const newNode = { id: nextId, type: nodeType, position, data: { label, params } };
      setNodes((nds) => nds.concat(newNode));
      setSelectedNodeId(nextId);
    },
    [reactFlowInstance, setNodes]
  );

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
        <Btn v="ghost" sz="sm" Icon={ArrowLeft} onClick={onBack}>Back</Btn>
        <span style={{ color: C.t3 }}>/</span>
        <h1 style={{ color: C.t1, fontWeight: 900, fontSize: 16 }}>Strategy Builder — {strategyName}</h1>

        {/* Mode Toggle */}
        <div style={{
          display: "flex",
          background: C.bg2,
          border: `1px solid ${C.border}`,
          borderRadius: 8,
          padding: 2,
          marginLeft: "auto"
        }}>
          <button
            onClick={() => setBuilderMode('live')}
            style={{
              padding: "6px 14px",
              borderRadius: 6,
              border: "none",
              background: builderMode === 'live' ? C.bg3 : 'transparent',
              color: builderMode === 'live' ? C.profit : C.t3,
              fontSize: 11,
              fontFamily: "monospace",
              fontWeight: builderMode === 'live' ? 700 : 400,
              cursor: "pointer",
              transition: "all 0.2s",
              display: "flex",
              alignItems: "center",
              gap: 4
            }}
          >
            <Radio size={10} color={builderMode === 'live' ? C.profit : C.t3} />
            Live Mode
          </button>
        </div>

        <div style={{ marginLeft: 12, display: "flex", gap: 8 }}>
          <Btn v="outline" sz="sm" Icon={RotateCcw} onClick={handleUndo} disabled={pastStates.length === 0}>Undo</Btn>
          <Btn v="outline" sz="sm" onClick={handleSaveStrategy} disabled={isSavingStrategy}>{isSavingStrategy ? "Saving..." : saveState === "saved" ? "Saved" : saveState === "error" ? "Retry Save" : "Save"}</Btn>
          {builderMode !== 'backtest' && (
              <select 
                value={selectedDeployExchange} 
                onChange={(e) => setSelectedDeployExchange(e.target.value)}
                style={{ background: C.bg3, color: C.t1, border: `1px solid ${C.border}`, borderRadius: 4, padding: "0 8px", fontSize: 10, fontFamily: "monospace" }}
              >
                <option value="binance">Binance</option>
                <option value="bybit">Bybit</option>
                <option value="okx">OKX</option>
              </select>
            )}
            <Btn v={builderMode === 'backtest' ? "primary" : "outline"} sz="sm" Icon={builderMode === 'backtest' ? Play : Radio} onClick={builderMode === 'backtest' ? handleBacktest : handleDeployLive} disabled={isSavingStrategy}>
            {builderMode === 'backtest' ? "Backtest" : saveState === "deployed" ? "Deployed!" : "Deploy Live"}
          </Btn>
        </div>
      </div>


      <div style={{ display: "grid", gridTemplateColumns: "160px 1fr 200px", gap: 12, height: 480 }}>
        {/* Toolbox */}
        <Card cls="p-3 overflow-y-auto" style={{ scrollbarWidth: "none" }}>
          <div style={{ color: C.t3, fontSize: 8, fontFamily: "monospace", fontWeight: 900, letterSpacing: 3, textTransform: "uppercase", marginBottom: 8 }}>TOOLBOX</div>
          {STRATEGY_TOOLBOX.map(sec => (
            <div key={sec.g} style={{ marginBottom: 12 }}>
              <div style={{ color: C.t2, fontSize: 8, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", marginBottom: 4 }}>{sec.g}</div>
              {sec.items.map(item => {
                // Handle both string items (legacy) and object items (new)
                const itemLabel = typeof item === 'string' ? item : item.label;
                const itemType = typeof item === 'string' ? sec.type : item.type;
                const isLiveOnly = itemType === 'orderbook' || itemType === 'liveticker';
                const isDisabled = isLiveOnly && builderMode === 'backtest';

                return (
                  <div
                    key={itemLabel}
                    draggable={!isDisabled}
                    onDragStart={(e) => !isDisabled && onDragStartToolbox(e, { nodeType: itemType, label: itemLabel })}
                    title={isDisabled ? "Live-only block - switch to Live Mode to use" : ""}
                    style={{
                      background: isDisabled ? C.bg2 : C.bg3,
                      border: `1px solid ${isDisabled ? C.border : C.border}`,
                      borderRadius: 6,
                      padding: "5px 8px",
                      marginBottom: 3,
                      fontSize: 10,
                      fontFamily: "monospace",
                      color: isDisabled ? C.t4 : C.t1,
                      cursor: isDisabled ? "not-allowed" : "grab",
                      transition: "all 0.15s",
                      userSelect: "none",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      opacity: isDisabled ? 0.5 : 1
                    }}
                    className={isDisabled ? "" : "hover:border-cyan-500/40 hover:text-cyan-400"}
                  >
                    <span>{itemLabel}</span>
                    {isLiveOnly && (
                      <span style={{
                        fontSize: 7,
                        color: isDisabled ? C.t4 : C.accent,
                        background: isDisabled ? 'transparent' : `${C.accent}20`,
                        padding: "1px 4px",
                        borderRadius: 3
                      }}>
                        {isDisabled ? "LIVE ONLY" : "LIVE"}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </Card>

        {/* Canvas */}
        <Card cls="p-0 relative overflow-hidden">
          <div ref={reactFlowWrapper} style={{ width: "100%", height: "100%", background: C.bg2 }}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onNodeClick={(_, node) => setSelectedNodeId(node.id)}
              onSelectionChange={({ nodes: selNodes }) => {
                setSelectedNodeId(selNodes?.[0]?.id || null);
              }}
              onPaneClick={() => setSelectedNodeId(null)}
              onDrop={onDropCanvas}
              onDragOver={onDragOverCanvas}
              nodeTypes={strategyNodeTypes}
              onInit={setReactFlowInstance}
              deleteKeyCode={['Backspace', 'Delete']}
              fitView
              proOptions={{ hideAttribution: true }}
              style={{ background: C.bg2 }}
            >
              <Background color={`${C.border}aa`} gap={26} />
              <Controls
                style={{
                  background: C.bg3,
                  border: `1px solid ${C.border}`,
                  borderRadius: 8,
                  overflow: "hidden",
                }}
              />
            </ReactFlow>
          </div>
        </Card>

        {/* Properties */}
        <Card cls="p-4">
          <PanelTitle title="Properties" />
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div>
              <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 3, textTransform: "uppercase", display: "block", marginBottom: 4 }}>Indicator</label>
              <div style={{ background: selectedNode?.type === "mlmodel" ? C.purple : selectedNode?.type === "operator" ? C.gold : C.cyan, color: "#000", borderRadius: 6, padding: "5px 10px", fontSize: 10, fontFamily: "monospace", fontWeight: 900, display: "inline-block" }}>
                {selectedNode?.data?.label || "Select node"}
              </div>
            </div>
            {/* Mode Indicator */}
            <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px" }}>
              <div style={{ color: C.t2, fontSize: 8, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 2 }}>Pipeline Mode</div>
              <div style={{
                color: builderMode === 'live' ? C.profit : C.t1,
                fontSize: 11,
                fontWeight: 700,
                display: "flex",
                alignItems: "center",
                gap: 6
              }}>
                <Radio size={12} color={builderMode === 'live' ? C.profit : C.t3} />
                {builderMode === 'live' ? 'LIVE DEPLOYMENT' : 'BACKTEST MODE'}
                {builderMode === 'live' && (
                  <span style={{
                    fontSize: 8,
                    color: isLiveConnected ? C.profit : C.warning,
                    background: isLiveConnected ? `${C.profit}20` : `${C.warning}20`,
                    padding: "1px 4px",
                    borderRadius: 3,
                    marginLeft: 4
                  }}>
                    {isLiveConnected ? 'CONNECTED' : 'CONNECTING...'}
                  </span>
                )}
              </div>
            </div>

            {/* Exchange info display (auto-fetched from vault) */}
            {activeExchange && (
              <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px" }}>
                <div style={{ color: C.t2, fontSize: 8, fontFamily: "monospace", textTransform: "uppercase", marginBottom: 2 }}>Connected Exchange</div>
                <div style={{ color: C.accent, fontSize: 11, fontWeight: 700, display: "flex", alignItems: "center", gap: 6 }}>
                  <Server size={12} />
                  {activeExchange.name.toUpperCase()}
                  {activeExchange.isDefault && <span style={{ fontSize: 8, color: C.warning, background: `${C.warning}20`, padding: "1px 4px", borderRadius: 3 }}>DEFAULT</span>}
                </div>
              </div>
            )}

            {/* Validation Errors */}
            {validationErrors.length > 0 && (
              <div style={{ background: `${C.loss}15`, border: `1px solid ${C.loss}40`, borderRadius: 8, padding: "8px 10px" }}>
                <div style={{ color: C.loss, fontSize: 9, fontFamily: "monospace", fontWeight: 700, marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
                  <AlertTriangle size={10} />
                  VALIDATION ERRORS
                </div>
                {validationErrors.map((err, i) => (
                  <div key={i} style={{ color: C.loss, fontSize: 9, marginLeft: 14 }}>• {err.message}</div>
                ))}
              </div>
            )}

            {/* Pipeline Errors */}
            {pipelineError && (
              <div style={{ background: `${C.loss}15`, border: `1px solid ${C.loss}40`, borderRadius: 8, padding: "8px 10px" }}>
                <div style={{ color: C.loss, fontSize: 9, fontFamily: "monospace", fontWeight: 700, marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
                  <AlertTriangle size={10} />
                  {pipelineError.type?.toUpperCase() || 'ERROR'}
                </div>
                <div style={{ color: C.loss, fontSize: 10, marginLeft: 14 }}>{pipelineError.message}</div>
                {pipelineError.type === 'validation' && pipelineError.details && (
                  <div style={{ marginTop: 4 }}>
                    {pipelineError.details.map((err, i) => (
                      <div key={i} style={{ color: C.t2, fontSize: 8, marginLeft: 14 }}>• {err.field}: {err.message}</div>
                    ))}
                  </div>
                )}
                <button
                  onClick={() => setPipelineError(null)}
                  style={{
                    marginTop: 8,
                    marginLeft: 14,
                    padding: "2px 8px",
                    background: 'transparent',
                    border: `1px solid ${C.loss}60`,
                    borderRadius: 4,
                    color: C.loss,
                    fontSize: 8,
                    fontFamily: 'monospace',
                    cursor: 'pointer'
                  }}
                >
                  Dismiss
                </button>
              </div>
            )}

            {/* Data Fetch Progress */}
            {isFetchingData && (
              <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px" }}>
                <div style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", marginBottom: 6, display: "flex", alignItems: "center", gap: 6 }}>
                  <Loader2 size={10} style={{ animation: "spin 1s linear infinite" }} />
                  Fetching historical data...
                </div>
                <div style={{ height: 4, background: C.bg2, borderRadius: 2, overflow: "hidden" }}>
                  <div style={{ width: `${fetchProgress}%`, height: "100%", background: C.accent, transition: "width 0.3s" }} />
                </div>
                <div style={{ color: C.t3, fontSize: 8, marginTop: 4, textAlign: "right" }}>{Math.round(fetchProgress)}%</div>
              </div>
            )}

            {/* Indicator Computation Status */}
            {isComputing && (
              <div style={{ background: C.bg3, border: `1px solid ${C.accent}40`, borderRadius: 8, padding: "10px" }}>
                <div style={{ color: C.accent, fontSize: 9, fontFamily: "monospace", display: "flex", alignItems: "center", gap: 6 }}>
                  <Loader2 size={10} style={{ animation: "spin 1s linear infinite" }} />
                  Computing indicators via backend...
                </div>
              </div>
            )}

            {/* Computation Error */}
            {computationError && (
              <div style={{ background: `${C.loss}15`, border: `1px solid ${C.loss}40`, borderRadius: 8, padding: "8px 10px" }}>
                <div style={{ color: C.loss, fontSize: 9, fontFamily: "monospace", fontWeight: 700, marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
                  <AlertTriangle size={10} />
                  INDICATOR ERROR
                </div>
                <div style={{ color: C.loss, fontSize: 10 }}>{computationError.message}</div>
                <div style={{ color: C.t2, fontSize: 8, marginTop: 2 }}>{computationError.indicator}</div>
              </div>
            )}

            {/* Logic Evaluation Status */}
            {isEvaluating && (
              <div style={{ background: C.bg3, border: `1px solid ${C.gold}40`, borderRadius: 8, padding: "10px" }}>
                <div style={{ color: C.gold, fontSize: 9, fontFamily: "monospace", display: "flex", alignItems: "center", gap: 6 }}>
                  <Loader2 size={10} style={{ animation: "spin 1s linear infinite" }} />
                  Evaluating logic conditions...
                </div>
              </div>
            )}

            {/* Logic Evaluation Error */}
            {evaluationError && (
              <div style={{ background: `${C.loss}15`, border: `1px solid ${C.loss}40`, borderRadius: 8, padding: "8px 10px" }}>
                <div style={{ color: C.loss, fontSize: 9, fontFamily: "monospace", fontWeight: 700, marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
                  <AlertTriangle size={10} />
                  LOGIC ERROR
                </div>
                <div style={{ color: C.loss, fontSize: 10 }}>{evaluationError.message}</div>
              </div>
            )}

            {/* Strategy Execution Status */}
            {isExecuting && (
              <div style={{ background: C.bg3, border: `1px solid ${C.purple}40`, borderRadius: 8, padding: "10px" }}>
                <div style={{ color: C.purple, fontSize: 9, fontFamily: "monospace", display: "flex", alignItems: "center", gap: 6 }}>
                  <Loader2 size={10} style={{ animation: "spin 1s linear infinite" }} />
                  Executing strategy via backend...
                </div>
                <div style={{ color: C.t2, fontSize: 8, marginTop: 4 }}>
                  Single API call: POST /strategy/execute
                </div>
              </div>
            )}

            {/* Execution Error */}
            {executionError && (
              <div style={{ background: `${C.loss}15`, border: `1px solid ${C.loss}40`, borderRadius: 8, padding: "8px 10px" }}>
                <div style={{ color: C.loss, fontSize: 9, fontFamily: "monospace", fontWeight: 700, marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
                  <AlertTriangle size={10} />
                  EXECUTION ERROR
                </div>
                <div style={{ color: C.loss, fontSize: 10 }}>{executionError.message}</div>
                <div style={{ color: C.t2, fontSize: 8, marginTop: 2 }}>
                  Type: {executionError.type}
                </div>
              </div>
            )}

            {/* Last Execution Result */}
            {lastExecution && (
              <div style={{ background: `${C.purple}15`, border: `1px solid ${C.purple}40`, borderRadius: 8, padding: "8px 10px" }}>
                <div style={{ color: C.purple, fontSize: 9, fontFamily: "monospace", fontWeight: 700, marginBottom: 4 }}>
                  LAST EXECUTION
                </div>
                <div style={{ color: C.t1, fontSize: 10 }}>
                  ID: {lastExecution.executionId?.slice(0, 16)}...
                </div>
                <div style={{ color: C.t2, fontSize: 8, marginTop: 2 }}>
                  Status: {lastExecution.status}
                </div>
                {lastExecution.metrics && (
                  <div style={{ color: C.t2, fontSize: 8, marginTop: 2 }}>
                    Signals: {lastExecution.signals?.length || 0}, Trades: {lastExecution.trades?.length || 0}
                  </div>
                )}
              </div>
            )}

            {/* Last Signal Display */}
            {lastSignal && (
              <div style={{ background: `${C.gold}15`, border: `1px solid ${C.gold}40`, borderRadius: 8, padding: "8px 10px" }}>
                <div style={{ color: C.gold, fontSize: 9, fontFamily: "monospace", fontWeight: 700, marginBottom: 4 }}>
                  LAST SIGNAL
                </div>
                <div style={{ color: lastSignal.signal === 'BUY' ? C.profit : lastSignal.signal === 'SELL' ? C.loss : C.t1, fontSize: 14, fontWeight: 900 }}>
                  {lastSignal.signal}
                </div>
                {lastSignal.confidence > 0 && (
                  <div style={{ color: C.t2, fontSize: 8, marginTop: 2 }}>
                    Confidence: {Math.round(lastSignal.confidence * 100)}%
                  </div>
                )}
              </div>
            )}

            {selectedNode ? (
              getNodeParamSchema(selectedNode?.data?.label, selectedNode?.type).map((field) =>
                renderDynamicField(field, selectedNode)
              )
            ) : (
              <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>Select a node to configure parameters.</div>
            )}
            <Inp lbl="Source" ph="Close" />
            <Inp lbl="Strategy Name" ph="Untitled Strategy" val={strategyName} onChange={e => setStrategyName(e.target.value)} />
            {selectedNode && (
              <button
                onClick={handleDeleteNode}
                style={{
                  width: "100%",
                  background: `${C.red}20`,
                  color: C.red,
                  fontWeight: 900,
                  borderRadius: 8,
                  padding: "10px 0",
                  border: `1px solid ${C.red}40`,
                  cursor: "pointer",
                  fontSize: 11,
                  fontFamily: "monospace",
                  transition: "all 0.2s"
                }}
                className="hover:bg-red-500/30"
              >
                Delete Block
              </button>
            )}
            <div>
              <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 3, textTransform: "uppercase", display: "block", marginBottom: 4 }}>Risk per Trade</label>
              <div style={{ display: "flex", gap: 4 }}>
                {["0.5%", "1%", "2%", "5%"].map(r => (
                  <button key={r} style={{ flex: 1, background: r === "1%" ? C.cyan + "20" : "transparent", color: r === "1%" ? C.cyan : C.t3, border: `1px solid ${r === "1%" ? C.cyan + "40" : C.border}`, borderRadius: 5, padding: "4px 0", fontSize: 9, fontFamily: "monospace", cursor: "pointer" }}>{r}</button>
                ))}
              </div>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

// Wrapper component that provides all strategy contexts
function StrategyBuilder(props) {
  return (
    <DataPipelineProvider>
      <IndicatorEngineProvider>
        <LogicEngineProvider>
          <StrategyEngineProvider>
            <StrategyBuilderInner {...props} />
          </StrategyEngineProvider>
        </LogicEngineProvider>
      </IndicatorEngineProvider>
    </DataPipelineProvider>
  );
}

function Backtester({ strategy, onBack }) {
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [timeframe, setTimeframe] = useState("15m");
  const [lookbackDays, setLookbackDays] = useState("30");
  const [initialCapital, setInitialCapital] = useState("10000");
  const [tradeSizePct, setTradeSizePct] = useState("10");
  const [mlThreshold, setMlThreshold] = useState(0.7);
  const [stopLossPct, setStopLossPct] = useState("2");
  const [takeProfitPct, setTakeProfitPct] = useState("4");
  const [isRunning, setIsRunning] = useState(false);
  const [results, setResults] = useState(null);
  const [supportedSymbols, setSupportedSymbols] = useState([]);
  const strategyName = strategy?.name || "Custom Strategy";

  useEffect(() => {
    const controller = new AbortController();
    const fetchSymbols = async () => {
      try {
        // Attempt to fetch supported exchanges from CCXT via FastAPI
        // Note: Market symbols are now handled via exchange-specific endpoints
        const data = await endpoints.exchange.getSupported();
        const symbols = Array.isArray(data) ? data : data?.supported || [];
        if (symbols.length > 0) {
          // Use default symbol list as exchanges don't provide full symbol lists directly
          setSupportedSymbols([
            "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
            "ADA/USDT", "DOGE/USDT", "DOT/USDT", "LINK/USDT", "AVAX/USDT",
            "MATIC/USDT", "LTC/USDT", "BCH/USDT", "UNI/USDT", "ATOM/USDT", "XLM/USDT", "NEAR/USDT",
            "APT/USDT", "OP/USDT", "ARB/USDT", "INJ/USDT", "RENDER/USDT", "SUI/USDT", "SEI/USDT", "FET/USDT", "PEPE/USDT",
            "WIF/USDT", "BONK/USDT", "FLOKI/USDT", "FTM/USDT", "TIA/USDT", "STX/USDT", "RNDR/USDT", "GALA/USDT", "ORDI/USDT"
          ].sort());
        }
      } catch (err) {
        // Comprehensive fallback list if endpoint isn't fully exposed yet
        setSupportedSymbols([
          "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "SHIB/USDT",
          "DOT/USDT", "LINK/USDT", "MATIC/USDT", "LTC/USDT", "BCH/USDT", "UNI/USDT", "ATOM/USDT", "XLM/USDT", "NEAR/USDT",
          "APT/USDT", "OP/USDT", "ARB/USDT", "INJ/USDT", "RENDER/USDT", "SUI/USDT", "SEI/USDT", "FET/USDT", "PEPE/USDT",
          "WIF/USDT", "BONK/USDT", "FLOKI/USDT", "FTM/USDT", "TIA/USDT", "STX/USDT", "RNDR/USDT", "GALA/USDT", "ORDI/USDT"
        ].sort());
      }
    };
    fetchSymbols();
    return () => controller.abort();
  }, []);

  // Convert DAG nodes to backend strategy names
  const extractStrategiesFromNodes = (nodes) => {
    const strategyMap = {
      // Indicator nodes map to strategy names
      'RSI': 'rsi',
      'MACD': 'macd',
      'SMA': 'sma_crossover',
      'EMA': 'ema_crossover',
      'Bollinger Bands': 'bollinger',
      'Stochastic': 'stochastic',
      'ATR': 'atr',
      'CCI': 'cci',
      'Williams %R': 'williams_r',
      'ADX': 'adx',
      'Supertrend': 'supertrend',
      'VWAP': 'vwap',
      'Momentum': 'momentum',
      'ROC': 'roc',
      // ML models
      'XGBoost': 'xgboost',
      'LSTM': 'lstm',
      'Transformer': 'transformer',
    };

    const strategies = [];
    (nodes || []).forEach(node => {
      if (node.type === 'indicator') {
        const label = node.data?.label;
        if (strategyMap[label]) {
          strategies.push(strategyMap[label]);
        }
      }
      // Add composite strategies for logic nodes
      if (node.type === 'logic') {
        const leftInd = node.data?.params?.left_indicator;
        const rightInd = node.data?.params?.right_indicator || 'SMA';
        if (leftInd && strategyMap[leftInd]) {
          strategies.push(strategyMap[leftInd]);
        }
      }
    });

    // Default to RSI if no strategies found
    return strategies.length > 0 ? strategies : ['rsi'];
  };

  // Extract symbol from source nodes or strategy data
  const extractSymbolsFromNodes = (nodes) => {
    const sourceNode = (nodes || []).find(n => n.type === 'source');
    if (sourceNode?.data?.params?.symbol) {
      // Normalize symbol to backend format (BTC/USDT -> BTCUSDT)
      const sym = sourceNode.data.params.symbol;
      return [sym.replace('/', '')];
    }
    // Fallback to strategy symbol
    if (strategy?.dataSource?.symbol) {
      return [strategy.dataSource.symbol.replace('/', '')];
    }
    return ['BTCUSDT'];
  };

  const runBacktest = async () => {
    if (!strategy) return;
    setIsRunning(true);
    try {
      // Extract strategies and symbols from DAG nodes
      const strategies = extractStrategiesFromNodes(strategy.nodes);
      const symbols = extractSymbolsFromNodes(strategy.nodes);

      const { nodes: serNodes, edges: serEdges } = serializeReactFlowToDAG(strategy.nodes || [], strategy.edges || []);

      // Build backend-compatible payload (matches BacktestRequest schema)
      const payload = {
        // Required: list of strategy names from registry
        strategies,
        // Required: list of symbols to trade
        symbols,
        // Timeframe for data fetch
        timeframe,
        // Backtest parameters (convert percentages to decimals)
        initial_capital: Number(initialCapital),
        trade_size_pct: Number(tradeSizePct) / 100,  // 10 -> 0.1
        stop_loss_pct: Number(stopLossPct) / 100,    // 2 -> 0.02
        take_profit_pct: Number(takeProfitPct) / 100, // 4 -> 0.04
        ml_threshold: Number(mlThreshold),
        params: {},
        dag: {
          nodes: serNodes,
          edges: serEdges,
          symbols: symbols,
          timeframe: timeframe,
          strategy_name: strategyName
        }
      };

      console.log("🔬 API CALL: POST /api/strategies/backtest", payload);
      console.log("🔬 DAG → Strategies mapping:", strategies);
      console.log("🔬 Extracted symbols:", symbols);

      // Route through secure global API interceptor
      const data = await endpoints.strategies.backtest(payload);
      console.log("🔬 API RESPONSE:", data);
      setResults(data);
    } catch (err) {
      console.error("🔬 API ERROR: Backtest run failed:", err.message);
      setResults(null);
    } finally {
      setIsRunning(false);
    }
  };

  const statItems = [
    { key: "total_return_pct", label: "Total Return %" },
    { key: "win_rate_pct", label: "Win Rate %" },
    { key: "max_drawdown_pct", label: "Max Drawdown %" },
    { key: "total_trades", label: "Total Trades" },
    { key: "profit_factor", label: "Profit Factor" },
    { key: "sharpe_ratio", label: "Sharpe Ratio" },
    { key: "sortino_ratio", label: "Sortino Ratio" },
    { key: "calmar_ratio", label: "Calmar Ratio" },
  ];

  const equityData = (results?.equity || []).map((row, i) => ({
    timestamp: row.timestamp ?? i,
    equity: Number(row.equity ?? 0),
  }));

  return (
    <div style={{ padding: 16, overflowY: "auto", flex: 1, display: "grid", gridTemplateColumns: "260px 1fr", gap: 12 }}>
      <Card cls="p-4" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <Btn v="ghost" sz="sm" Icon={ArrowLeft} onClick={onBack}>Back</Btn>
        <div style={{ color: C.t1, fontWeight: 900, fontSize: 15 }}>VectorBT Engine</div>
        <div style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>{strategy?.name || "Untitled Strategy"}</div>

        <div>
          <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", display: "block", marginBottom: 6 }}>Asset Pair</label>
          <input
            list="crypto-symbols"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="Search asset..."
            style={{ width: "100%", background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace", color: C.t1, outline: "none" }}
            className="focus:border-cyan-500/50 transition-all"
          />
          <datalist id="crypto-symbols">
            {supportedSymbols.map(s => <option key={s} value={s} />)}
          </datalist>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <div>
            <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", display: "block", marginBottom: 6 }}>Timeframe</label>
            <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} style={{ width: "100%", background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 8, padding: "8px 10px", fontSize: 11, fontFamily: "monospace", color: C.t1, outline: "none" }}>
              {["1m", "5m", "15m", "1h", "4h", "1d"].map(tf => <option key={tf} value={tf}>{tf}</option>)}
            </select>
          </div>
          <Inp lbl="Days" type="number" val={lookbackDays} onChange={(e) => setLookbackDays(e.target.value)} />
        </div>

        <Inp lbl="Capital ($)" type="number" val={initialCapital} onChange={(e) => setInitialCapital(e.target.value)} />
        <Inp lbl="Trade Size %" type="number" val={tradeSizePct} onChange={(e) => setTradeSizePct(e.target.value)} />

        <div>
          <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase", display: "block", marginBottom: 6 }}>ML Confidence: {Number(mlThreshold).toFixed(2)}</label>
          <input type="range" min={0.5} max={0.99} step={0.01} value={mlThreshold} onChange={(e) => setMlThreshold(e.target.value)} style={{ width: "100%", accentColor: C.cyan }} />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <Inp lbl="Stop Loss %" type="number" val={stopLossPct} onChange={(e) => setStopLossPct(e.target.value)} />
          <Inp lbl="Take Profit %" type="number" val={takeProfitPct} onChange={(e) => setTakeProfitPct(e.target.value)} />
        </div>

        <button onClick={runBacktest} disabled={isRunning || !strategy} style={{ marginTop: 8, background: C.cyan, color: "#000", borderRadius: 10, padding: "14px 0", fontSize: 12, fontFamily: "monospace", fontWeight: 900, letterSpacing: 1, cursor: isRunning ? "wait" : "pointer", border: "none", opacity: isRunning ? 0.75 : 1, transition: "all 0.2s" }}>
          {isRunning ? "Running VectorBT..." : "RUN BACKTEST"}
        </button>
      </Card>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 }}>
          {statItems.map((s) => (
            <Card key={s.key} cls="p-3">
              <div style={{ color: C.t3, fontSize: 8, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase", marginBottom: 4 }}>{s.label}</div>
              <div style={{ color: C.t1, fontSize: 18, fontWeight: 900, fontFamily: "monospace" }}>
                {results?.[s.key] === null || results?.[s.key] === undefined ? "-" : String(results[s.key])}
              </div>
            </Card>
          ))}
        </div>
        <Card cls="p-4 flex-1 flex flex-col">
          <PanelTitle title="Equity Curve" sub={results ? `Simulated performance over ${lookbackDays} days` : "Awaiting backtest execution..."} />
          <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={equityData}>
                <defs>
                  <linearGradient id="btEq" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={C.green} stopOpacity={0.22} />
                    <stop offset="95%" stopColor={C.green} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                <XAxis dataKey="timestamp" hide />
                <YAxis domain={["auto", "auto"]} hide />
                <Tooltip content={<CustomTooltip prefix="$" />} />
                <Area dataKey="equity" stroke={C.green} strokeWidth={2} fill="url(#btEq)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
            {!results && !isRunning && (
              <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: C.t3, fontFamily: "monospace", fontSize: 12 }}>
                Configure parameters and click Run Backtest to visualize data.
              </div>
            )}
            {isRunning && (
              <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: C.cyan, fontFamily: "monospace", fontSize: 12, background: "rgba(1,6,8,0.5)", backdropFilter: "blur(2px)" }}>
                VectorBT Engine is crunching historical data...
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  PAGE: PORTFOLIO
// ═══════════════════════════════════════════════════════════════════
function Portfolio() {
  const [summary, setSummary] = useState(null);
  const [equityCurve, setEquityCurve] = useState([]);
  const [allocation, setAllocation] = useState([]);
  const [heatmapData, setHeatmapData] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const COLORS = [C.orange, C.purple, C.cyan, C.gold, C.t3];

  useEffect(() => {
    const toNumber = (v, fallback = 0) => {
      const n = Number(v);
      return Number.isFinite(n) ? n : fallback;
    };

    const money = (n) => {
      const num = toNumber(n, 0);
      return `${num >= 0 ? "$" : "-$"}${Math.abs(num).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
    };

    const pct = (n) => `${toNumber(n, 0).toFixed(1)}%`;

    const loadPortfolio = async () => {
      try {
        // Portfolio endpoints not available in backend - using empty data
        setSummary({});
        setEquityCurve([]);
        setAllocation([]);
        setHeatmapData([]);
      } catch (error) {
      } finally {
        setIsLoading(false);
      }
    };

    loadPortfolio();
  }, []);

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      <SectionH title="Portfolio Overview" sub="Real-time asset distribution and performance analytics" />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 16 }}>
        <Card cls="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{ position: "absolute", top: 0, right: 0, width: 70, height: 70, background: "radial-gradient(circle, rgba(0,212,255,0.1) 0%, transparent 70%)" }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Total Value</span>
            <DollarSign size={13} style={{ color: C.cyan }} />
          </div>
          <div style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>
            {isLoading ? "Loading..." : (summary ? `${summary.total_value?.toLocaleString(undefined, { maximumFractionDigits: 2 }) || "0.00"}` : "$0.00")}
          </div>
        </Card>
        <Card cls="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{ position: "absolute", top: 0, right: 0, width: 70, height: 70, background: "radial-gradient(circle, rgba(0,212,255,0.1) 0%, transparent 70%)" }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Unrealized P&L</span>
            <TrendingUp size={13} style={{ color: C.cyan }} />
          </div>
          <div style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>
            {isLoading ? "Loading..." : (summary ? `${summary.unrealized_pnl?.toLocaleString(undefined, { maximumFractionDigits: 2 }) || "0.00"}` : "$0.00")}
          </div>
        </Card>
        <Card cls="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{ position: "absolute", top: 0, right: 0, width: 70, height: 70, background: "radial-gradient(circle, rgba(0,212,255,0.1) 0%, transparent 70%)" }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>Realized P&L</span>
            <CheckCircle size={13} style={{ color: C.cyan }} />
          </div>
          <div style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>
            {isLoading ? "Loading..." : (summary ? `${summary.realized_pnl?.toLocaleString(undefined, { maximumFractionDigits: 2 }) || "0.00"}` : "$0.00")}
          </div>
        </Card>
        <Card cls="p-4 relative overflow-hidden hover:border-cyan-500/20 transition-all">
          <div style={{ position: "absolute", top: 0, right: 0, width: 70, height: 70, background: "radial-gradient(circle, rgba(0,212,255,0.1) 0%, transparent 70%)" }} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>ROI</span>
            <Percent size={13} style={{ color: C.cyan }} />
          </div>
          <div style={{ color: C.t1, fontSize: 20, fontWeight: 900 }}>
            {isLoading ? "Loading..." : (summary ? `${summary.roi_percentage?.toFixed(1) || "0.0"}%` : "0.0%")}
          </div>
        </Card>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 260px", gap: 12, marginBottom: 12 }}>
        {/* Equity Curve */}
        <Card cls="p-4">
          <PanelTitle title="Equity Curve" sub="90-day portfolio growth" />
          {equityCurve.length === 0 ? (
            <div style={{ height: 240, display: "flex", alignItems: "center", justifyContent: "center", color: C.t3, fontFamily: "monospace", fontSize: 12 }}>
              No data available yet
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={equityCurve}>
                <defs>
                  <linearGradient id="eg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={C.green} stopOpacity={0.2} />
                    <stop offset="95%" stopColor={C.green} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                <XAxis dataKey="date" hide />
                <YAxis domain={["auto", "auto"]} hide />
                <Tooltip content={<CustomTooltip prefix="$" />} />
                <Area dataKey="value" stroke={C.green} strokeWidth={1.5} fill="url(#eg)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>

        {/* Asset Allocation */}
        <Card cls="p-4">
          <PanelTitle title="Asset Allocation" />
          {allocation.length === 0 ? (
            <div style={{ height: 240, display: "flex", alignItems: "center", justifyContent: "center", color: C.t3, fontFamily: "monospace", fontSize: 12 }}>
              No data available yet
            </div>
          ) : (
            <>
              <div style={{ display: "flex", justifyContent: "center", marginBottom: 8 }}>
                <PieChart width={160} height={160}>
                  <Pie data={allocation} cx={80} cy={80} innerRadius={50} outerRadius={75} dataKey="percentage" strokeWidth={0}>
                    {allocation.map((e, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                  </Pie>
                </PieChart>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                {allocation.map((a, i) => (
                  <div key={a.asset} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div style={{ width: 8, height: 8, borderRadius: 2, background: COLORS[i % COLORS.length], flexShrink: 0 }} />
                    <span style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", flex: 1 }}>{a.asset}</span>
                    <span style={{ color: C.t1, fontSize: 10, fontFamily: "monospace", fontWeight: 700 }}>{a.percentage.toFixed(1)}%</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </Card>
      </div>

      {/* P&L Heatmap */}
      <Card cls="p-4">
        <PanelTitle title="P&L Heatmap" sub="Daily performance calendar" />
        {heatmapData.length === 0 ? (
          <div style={{ height: 240, display: "flex", alignItems: "center", justifyContent: "center", color: C.t3, fontFamily: "monospace", fontSize: 12 }}>
            No data available yet
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gap: 3 }}>
            {["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"].map(d => <div key={d} style={{ color: C.t3, fontSize: 8, fontFamily: "monospace", textAlign: "center", padding: "2px 0", letterSpacing: 1 }}>{d}</div>)}
            {heatmapData.map((d, i) => (
              <div key={i} style={{
                height: 28, borderRadius: 4,
                background: d.pnl === null ? C.bg3 : d.pnl > 200 ? `${C.green}aa` : d.pnl > 50 ? `${C.green}55` : d.pnl > 0 ? `${C.green}22` : d.pnl < -200 ? `${C.red}aa` : d.pnl < -50 ? `${C.red}55` : `${C.red}22`,
                border: `1px solid ${d.pnl === null ? C.border : d.pnl > 0 ? `${C.green}40` : `${C.red}40`}`,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 8, fontFamily: "monospace", color: d.pnl === null ? C.t4 : d.pnl > 0 ? C.green : C.red,
                cursor: d.pnl !== null ? "pointer" : "default", fontWeight: 700,
              }} title={d.pnl !== null ? `${d.pnl >= 0 ? "+" : ""}$${d.pnl.toFixed(2)}` : ""}>
                {d.pnl !== null ? (d.pnl >= 0 ? "+" : "") + d.pnl.toFixed(0) : ""}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  PAGE: TRADE HISTORY (LEDGER)
// ═══════════════════════════════════════════════════════════════════
function TradeHistory() {
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeFilter, setActiveFilter] = useState("ALL");
  const [error, setError] = useState(null);

  useEffect(() => {
    const controller = new AbortController();

    const toNumber = (v, fallback = 0) => {
      const n = Number(v);
      return Number.isFinite(n) ? n : fallback;
    };

    const normalizeTrades = (rows = []) =>
      (Array.isArray(rows) ? rows : []).map((row, i) => ({
        id: row.id ?? row.trade_id ?? i + 1,
        pair: row.pair ?? row.symbol ?? "N/A",
        side: String(row.side ?? "buy").toLowerCase(),
        entry: toNumber(row.entry ?? row.entry_price),
        exit: toNumber(row.exit ?? row.exit_price),
        size: toNumber(row.size ?? row.quantity),
        pnl: toNumber(row.pnl ?? row.profit_loss),
        fees: toNumber(row.fees ?? row.fee),
        slip: toNumber(row.slip ?? row.slippage),
        strat: row.strat ?? row.strategy ?? row.strategy_name ?? "Direct",
        time: row.time ?? row.executed_at ?? row.timestamp ?? new Date().toISOString(),
      }));

    const loadTrades = async () => {
      setError(null);
      try {
        // Utilize globally authenticated api instance
        const data = await api.orders.getHistory();
        const rows = Array.isArray(data) ? data : data?.data || data?.trades || [];
        setTrades(normalizeTrades(rows));
      } catch (err) {
        if (err?.name !== "CanceledError" && err?.name !== "AbortError") {
          console.error("Failed loading trade history:", err);
          setError("Failed to fetch ledger. Please check backend connection.");
        }
      } finally {
        setLoading(false);
      }
    };

    loadTrades();
    return () => controller.abort();
  }, []);

  const filtered = trades.filter((t) =>
    activeFilter === "ALL" ||
    (activeFilter === "BUY" && t.side === "buy") ||
    (activeFilter === "SELL" && t.side === "sell") ||
    (activeFilter === "PROFIT" && t.pnl > 0)
  );

  const totalTrades = trades.length;
  const profitableTrades = trades.filter((t) => t.pnl > 0).length;
  const losingTrades = trades.filter((t) => t.pnl < 0).length;
  const winRate = totalTrades ? (profitableTrades / totalTrades) * 100 : 0;
  const totalPnl = trades.reduce((sum, t) => sum + (Number.isFinite(t.pnl) ? t.pnl : 0), 0);

  const exportFilteredToCsv = () => {
    const headers = ["ID", "Time", "Pair", "Side", "Entry", "Exit", "Size", "P&L", "Fees", "Slippage", "Strategy"];
    const escapeCsv = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const rows = filtered.map((t) => [
      t.id, t.time, t.pair, t.side, t.entry, t.exit, t.size, t.pnl, t.fees, t.slip, t.strat
    ]);
    const csv = [headers.join(","), ...rows.map((r) => r.map(escapeCsv).join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `algo22_ledger_${activeFilter.toLowerCase()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      <SectionH title="Trade Ledger" sub={`${trades.length} total trades — Institutional Execution Log`}
        right={<div style={{ display: "flex", gap: 6 }}><Btn v="outline" sz="sm" Icon={Filter}>Filter</Btn><Btn v="outline" sz="sm" Icon={Download} onClick={exportFilteredToCsv} disabled={filtered.length === 0}>Export CSV</Btn></div>} />

      {/* Summary Stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 8, marginBottom: 14 }}>
        {[
          { l: "Total Trades", v: loading ? "..." : totalTrades, c: C.cyan },
          { l: "Profitable", v: loading ? "..." : profitableTrades, c: C.green },
          { l: "Losing", v: loading ? "..." : losingTrades, c: C.red },
          { l: "Win Rate", v: loading ? "..." : `${winRate.toFixed(1)}%`, c: C.cyan },
          { l: "Total P&L", v: loading ? "..." : `$${totalPnl.toFixed(2)}`, c: totalPnl >= 0 ? C.green : C.red },
        ].map(s => (
          <Card key={s.l} cls="p-3">
            <div style={{ color: C.t3, fontSize: 8, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase", marginBottom: 3 }}>{s.l}</div>
            <div style={{ color: s.c, fontSize: 16, fontWeight: 900, fontFamily: "monospace" }}>{s.v}</div>
          </Card>
        ))}
      </div>

      {/* Filters & Errors */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ display: "flex", gap: 4 }}>
          {["ALL", "BUY", "SELL", "PROFIT"].map(f => (
            <button key={f} onClick={() => setActiveFilter(f)}
              style={{ background: activeFilter === f ? C.cyan + "20" : "transparent", color: activeFilter === f ? C.cyan : C.t3, border: `1px solid ${activeFilter === f ? C.cyan + "40" : C.border}`, borderRadius: 6, padding: "4px 12px", fontSize: 9, fontFamily: "monospace", fontWeight: 900, cursor: "pointer", letterSpacing: 2, textTransform: "uppercase" }}>
              {f}
            </button>
          ))}
        </div>
        {error && <span style={{ color: C.red, fontSize: 10, fontFamily: "monospace" }}>{error}</span>}
      </div>

      {/* Ledger Table */}
      <Card>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              {["#", "Time", "Pair", "Side", "Entry", "Exit", "Size", "P&L", "Fees", "Slippage", "Strategy"].map(h => (
                <th key={h} style={{ color: C.t3, fontWeight: 900, letterSpacing: 1.5, fontSize: 8, padding: "10px 12px", textAlign: "left", textTransform: "uppercase" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={11} style={{ padding: "20px", textAlign: "center", color: C.t3, fontFamily: "monospace" }}>Loading trade history from engine...</td>
              </tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={11} style={{ padding: "20px", textAlign: "center", color: C.t3, fontFamily: "monospace" }}>No trades found. Deploy a strategy to see ledger data.</td>
              </tr>
            ) : (
              filtered.map(t => (
                <tr key={t.id} style={{ borderBottom: `1px solid ${C.border}15` }} className="hover:bg-white/5 transition-colors cursor-pointer">
                  <td style={{ padding: "8px 12px", color: C.t3 }}>#{t.id}</td>
                  <td style={{ padding: "8px 12px", color: C.t3 }}>{String(t.time).includes('T') ? String(t.time).split('T')[1].slice(0, 8) : String(t.time).slice(11, 19)}</td>
                  <td style={{ padding: "8px 12px", color: C.t1, fontWeight: 700 }}>{t.pair}</td>
                  <td style={{ padding: "8px 12px" }}><Tag2 c={t.side === "buy" ? "green" : "red"}>{t.side.toUpperCase()}</Tag2></td>
                  <td style={{ padding: "8px 12px", color: C.t2 }}>${t.entry.toLocaleString()}</td>
                  <td style={{ padding: "8px 12px", color: C.t2 }}>${t.exit.toLocaleString()}</td>
                  <td style={{ padding: "8px 12px", color: C.t2 }}>{t.size}</td>
                  <td style={{ padding: "8px 12px", color: t.pnl >= 0 ? C.green : C.red, fontWeight: 700 }}>{t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(2)}</td>
                  <td style={{ padding: "8px 12px", color: C.t3 }}>${t.fees}</td>
                  <td style={{ padding: "8px 12px", color: C.t3 }}>{t.slip}%</td>
                  <td style={{ padding: "8px 12px", color: C.t2, fontSize: 9 }}>{String(t.strat).slice(0, 14)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

// ========================
//  PAGE: EXCHANGE MANAGER
// ========================
function ExchangeManager() {
  // Built-in fallback list of top CCXT exchanges to ensure UI is never empty
  const POPULAR_EXCHANGES = [
    "binance", "binanceus", "bybit", "okx", "kraken", "coinbasepro",
    "kucoin", "htx", "bitget", "gateio", "mexc", "bitfinex", "bitstamp",
    "gemini", "upbit", "deribit", "phemex", "woo", "bingx", "bitmart",
    "huobi", "crypto.com", "ascendex", "poloniex", "whitebit"
  ].sort();

  const [connectedExchanges, setConnectedExchanges] = useState([]);
  const [supportedExchanges, setSupportedExchanges] = useState(POPULAR_EXCHANGES);
  const [selectedExchange, setSelectedExchange] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [label, setLabel] = useState("");
  const [searchExchange, setSearchExchange] = useState("");
  const [loadingExchanges, setLoadingExchanges] = useState(true);
  const [isTesting, setIsTesting] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [processingById, setProcessingById] = useState({});
  const [toast, setToast] = useState(null);
  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);

  const normalizeConnected = (rows = []) =>
    (Array.isArray(rows) ? rows : []).map((row, i) => ({
      id: row.id ?? row.exchange_id ?? i + 1,
      name: row.name ?? row.exchange ?? "Unknown",
      key: row.key_masked ?? row.masked_key ?? "????????????????????????????",
      status: row.status ?? "connected",
      perms: row.perms ?? row.permissions ?? ["Spot Trading", "Read"],
      vol: row.vol ?? row.volume ?? "$0",
      ts: row.ts ?? row.created_at ?? "Just now",
    }));

  const loadConnectedExchanges = async (signal) => {
    try {
      const data = await endpoints.exchange.list();
      const rows = Array.isArray(data) ? data : data?.data || data?.exchanges || [];
      setConnectedExchanges(normalizeConnected(rows));
    } catch (err) {
      if (err?.name !== "CanceledError") console.error("Failed loading connected exchanges:", err);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    const loadAll = async () => {
      setLoadingExchanges(true);
      try {
        // 1. Fetch connected exchanges
        await loadConnectedExchanges(controller.signal);

        // 2. Fetch supported CCXT exchanges
        const supportedData = await endpoints.exchange.getSupported();
        const rawSupported = Array.isArray(supportedData) ? supportedData : supportedData?.supported || [];

        // Only overwrite the fallback if the backend successfully returns a valid array
        if (rawSupported.length > 0) {
          setSupportedExchanges(rawSupported);
        }
      } catch (err) {
        if (err && err.name !== "CanceledError") {
          console.warn("Backend CCXT list failed to load, utilizing built-in fallback list.");
        }
      } finally {
        setLoadingExchanges(false);
      }
    };
    loadAll();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(timer);
  }, [toast]);

  const handleTestConnection = async () => {
    if (!selectedExchange || !apiKey || !secretKey || isTesting) return;
    setIsTesting(true);
    try {
      await endpoints.exchange.testConnection({
        exchange_id: selectedExchange,
        api_key: apiKey,
        secret_key: secretKey,
        label
      });
      setToast({ type: "success", msg: "Connection verified via CCXT successfully." });
    } catch (err) {
      setToast({ type: "error", msg: err?.response?.data?.detail || "Connection test failed. Check API keys." });
    } finally {
      setIsTesting(false);
    }
  };

  const handleSaveKey = async () => {
    if (!selectedExchange || !apiKey || !secretKey || isSaving) return;
    setIsSaving(true);
    try {
      await endpoints.exchange.saveKeys({
        exchange_id: selectedExchange,
        api_key: apiKey,
        secret_key: secretKey,
        label
      });
      setToast({ type: "success", msg: "Exchange keys securely encrypted and stored in Vault." });

      // SECURE CLEANUP: Immediately wipe raw secrets from client state
      setApiKey("");
      setSecretKey("");
      setLabel("");
      setSelectedExchange("");

      // Refresh the list from the backend
      await loadConnectedExchanges();
    } catch (err) {
      setToast({ type: "error", msg: err?.response?.data?.detail || "Failed to save exchange keys." });
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeleteSaved = async (id) => {
    if (processingById[id]) return;
    setProcessingById((p) => ({ ...p, [id]: true }));
    try {
      await endpoints.exchange.delete(id);
      setConnectedExchanges((rows) => rows.filter((row) => row.id !== id));
      setToast({ type: "success", msg: "Exchange connection removed." });
    } catch (err) {
      setToast({ type: "error", msg: "Failed to delete exchange connection." });
    } finally {
      setProcessingById((p) => ({ ...p, [id]: false }));
    }
  };

  const filteredSupported = supportedExchanges.filter((ex) =>
    ex.toLowerCase().includes(searchExchange.toLowerCase())
  );

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1, position: "relative" }}>
      <SectionH title="Exchange Vault" sub="Manage institutional API connections via CCXT engine" />

      {/* Connected Exchanges */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 24 }}>
        {loadingExchanges ? (
          <Card cls="p-4"><div style={{ color: C.t3, fontFamily: "monospace", fontSize: 10 }}>Syncing with backend...</div></Card>
        ) : connectedExchanges.length === 0 ? (
          <Card cls="p-4 text-center"><div style={{ color: C.t3, fontFamily: "monospace", fontSize: 11, padding: "10px 0" }}>No active exchange connections found.</div></Card>
        ) : connectedExchanges.map(ex => (
          <Card key={ex.id} cls="p-5">
            <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
              <div style={{ background: "rgba(0,212,255,0.08)", border: "1px solid rgba(0,212,255,0.15)", borderRadius: 10, padding: 10, flexShrink: 0 }}>
                <Globe size={20} style={{ color: C.cyan }} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <span style={{ color: C.t1, fontWeight: 900, fontSize: 15, textTransform: "capitalize" }}>{ex.name}</span>
                  <Tag2 c="green">{ex.status.toUpperCase()}</Tag2>
                </div>
                <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>
                  <Key size={9} style={{ display: "inline", marginRight: 4 }} />{ex.key}
                  <span style={{ margin: "0 8px" }}>?</span>
                  <Clock size={9} style={{ display: "inline", marginRight: 4 }} />Added: {ex.ts}
                </div>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <Btn v="danger" sz="sm" Icon={Trash2} onClick={() => handleDeleteSaved(ex.id)} disabled={!!processingById[ex.id]} />
              </div>
            </div>
          </Card>
        ))}
      </div>

      {/* Add New Connection via CCXT */}
      <Card cls="p-6">
        <PanelTitle title="Connect New Exchange" sub="Select from over 100+ supported CCXT integrations. Keys are encrypted with AES-256." />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>

          {/* Left Column: CCXT Search & Scroll List */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 3, textTransform: "uppercase" }}>Supported Exchanges</label>
            <div style={{ position: "relative" }}>
              <Search size={12} style={{ color: C.t3, position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", zIndex: 1 }} />
              <input
                placeholder="Search CCXT network..."
                value={searchExchange}
                onChange={e => setSearchExchange(e.target.value)}
                style={{ width: "100%", background: C.bg3, border: `1px solid ${C.border}`, color: C.t1, padding: "8px 12px 8px 30px", borderRadius: 8, fontSize: 11, fontFamily: "monospace", outline: "none" }}
              />
            </div>
            <div style={{
              background: C.bg1, border: `1px solid ${C.border}`, borderRadius: 8,
              maxHeight: 220, overflowY: "auto", display: "flex", flexDirection: "column",
              scrollbarWidth: "thin", scrollbarColor: `${C.cyan}40 ${C.bg1}`
            }}>
              {filteredSupported.length === 0 ? (
                <div style={{ padding: 20, textAlign: "center", color: C.t3, fontSize: 10, fontFamily: "monospace" }}>No exchanges match your search.</div>
              ) : filteredSupported.map(ex => (
                <div
                  key={ex}
                  onClick={() => setSelectedExchange(ex)}
                  style={{
                    padding: "10px 14px", borderBottom: `1px solid ${C.border}55`, cursor: "pointer",
                    background: selectedExchange === ex ? `${C.cyan}20` : "transparent",
                    color: selectedExchange === ex ? C.cyan : C.t2,
                    fontSize: 11, fontFamily: "monospace", fontWeight: selectedExchange === ex ? 900 : 400,
                    textTransform: "capitalize", transition: "all 0.1s"
                  }}
                  className="hover:bg-cyan-500/10 hover:text-cyan-400"
                >
                  {ex}
                </div>
              ))}
            </div>
            {selectedExchange && (
              <div style={{ marginTop: 8, fontSize: 10, color: C.green, fontFamily: "monospace", display: "flex", alignItems: "center", gap: 6 }}>
                <CheckCircle size={10} /> Selected: <span style={{ fontWeight: 900, textTransform: "uppercase" }}>{selectedExchange}</span>
              </div>
            )}
          </div>

          {/* Right Column: Credentials Input */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <Inp lbl="API Key" ph="Enter exchange API key..." icon={Key} type="password" val={apiKey} onChange={e => setApiKey(e.target.value)} disabled={!selectedExchange} />
            <Inp lbl="Secret Key" ph="Enter exchange secret key..." icon={Lock} type="password" val={secretKey} onChange={e => setSecretKey(e.target.value)} disabled={!selectedExchange} />
            <Inp lbl="Label (optional)" ph="e.g., Main Binance Account" val={label} onChange={e => setLabel(e.target.value)} disabled={!selectedExchange} />

            <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
              <Btn v="outline" sz="sm" Icon={Wifi} cls="flex-1 justify-center" onClick={handleTestConnection} disabled={isTesting || isSaving || !selectedExchange}>
                {isTesting ? "Testing Engine..." : "Test Connection"}
              </Btn>
              <Btn v="primary" sz="sm" cls="flex-1 justify-center" onClick={handleSaveKey} disabled={isSaving || isTesting || !selectedExchange}>
                {isSaving ? "Encrypting..." : "Save Keys"}
              </Btn>
            </div>
          </div>
        </div>
      </Card>

      {/* Toast Notifications */}
      {toast && (
        <div style={{ position: "fixed", right: 24, bottom: 24, background: toast.type === "success" ? `${C.green}20` : `${C.red}20`, border: `1px solid ${toast.type === "success" ? `${C.green}55` : `${C.red}55`}`, color: toast.type === "success" ? C.green : C.red, borderRadius: 8, padding: "10px 16px", fontSize: 11, fontFamily: "monospace", fontWeight: 700, zIndex: 120, boxShadow: "0 10px 30px rgba(0,0,0,0.5)" }}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  PAGE: RISK SETTINGS
// ═══════════════════════════════════════════════════════════════════
function RiskSettings() {
  const [maxLoss, setMaxLoss] = useState(500);
  const [maxPos, setMaxPos] = useState(10);
  const [leverage, setLeverage] = useState(3);
  const [killSwitches, setKillSwitches] = useState([
    { key: "loss", l: "Stop all bots if daily loss > Max Limit", active: true },
    { key: "blackswan", l: "Halt trading on Black Swan event", active: true },
    { key: "streak", l: "Pause on 3 consecutive losses", active: false },
    { key: "capital", l: "Stop at 80% capital utilization", active: true },
  ]);
  const [strategyLimits, setStrategyLimits] = useState([]);
  const [marginData, setMarginData] = useState([
    { l: "Margin Ratio", v: 0, max: 100, c: C.green, key: "margin_ratio" },
    { l: "Free Margin", v: 0, max: 100, c: C.cyan, key: "free_margin" },
    { l: "Risk Score", v: 0, max: 100, c: C.orange, key: "risk_score" },
  ]);
  const [isLoadingRisk, setIsLoadingRisk] = useState(true);
  const [toast, setToast] = useState(null);
  const sliderDebounceRef = useRef(null);
  const strategyDebounceMapRef = useRef({});

  useEffect(() => {
    const controller = new AbortController();

    const loadRiskData = async () => {
      try {
        // Fetch all user-level risk data concurrently
        const [riskRes, limitsRes, marginRes] = await Promise.allSettled([
          api.risk.getConfig(),
          api.risk.getStrategyLimits(),
          api.risk.getMarginHealth()
        ]);

        // 1. Process Base Config & Kill Switches
        if (riskRes.status === 'fulfilled' && riskRes.value.data) {
          const cfg = riskRes.value.data;
          setMaxLoss(cfg.max_daily_loss ?? 500);
          setMaxPos(cfg.max_open_positions ?? 10);
          setLeverage(cfg.max_leverage ?? 3);

          if (cfg.kill_switches) {
            setKillSwitches(prev => prev.map(ks => ({
              ...ks,
              active: cfg.kill_switches[ks.key] ?? ks.active
            })));
          }
        }

        // 2. Process Strategy Capital Limits
        if (limitsRes.status === 'fulfilled' && limitsRes.value.data) {
          const limits = Array.isArray(limitsRes.value.data) ? limitsRes.value.data : [];
          setStrategyLimits(limits.map((s, i) => ({
            id: s.id ?? s.strategy_id ?? i + 1,
            name: s.name ?? s.strategy_name ?? `Strategy #${i + 1}`,
            allocationPct: Math.max(0, Math.min(100, Number(s.capital_limit_pct ?? 20))),
            capitalText: s.capital_text ?? `$${Number(s.capital_limit_usd ?? 0).toLocaleString()}`
          })));
        }

        // 3. Process Live Margin Health
        if (marginRes.status === 'fulfilled' && marginRes.value.data) {
          const m = marginRes.value.data;
          setMarginData([
            { l: "Margin Ratio", v: Number(m.margin_ratio ?? 0), max: 100, c: C.green, key: "margin_ratio" },
            { l: "Free Margin", v: Number(m.free_margin ?? 0), max: 100, c: C.cyan, key: "free_margin" },
            { l: "Risk Score", v: Number(m.risk_score ?? 0), max: 100, c: C.orange, key: "risk_score" },
          ]);
        }
      } catch (err) {
        console.error("Risk data load error:", err);
      } finally {
        setIsLoadingRisk(false);
      }
    };

    loadRiskData();
    return () => controller.abort();
  }, []);

  const saveRiskConfig = async (overrides = {}) => {
    try {
      await api.risk.updateConfig({
        max_daily_loss: maxLoss,
        max_open_positions: maxPos,
        max_leverage: leverage,
        kill_switches: killSwitches.reduce((acc, ks) => ({ ...acc, [ks.key]: ks.active }), {}),
        ...overrides,
      });
      setToast({ type: "success", msg: "Risk parameters updated safely." });
    } catch (err) {
      setToast({ type: "error", msg: "Failed to sync risk parameters." });
    }
  };

  const handleSliderChange = (key, value) => {
    if (key === "maxLoss") setMaxLoss(value);
    if (key === "maxPos") setMaxPos(value);
    if (key === "leverage") setLeverage(value);

    clearTimeout(sliderDebounceRef.current);
    sliderDebounceRef.current = setTimeout(() => {
      saveRiskConfig({
        max_daily_loss: key === "maxLoss" ? value : maxLoss,
        max_open_positions: key === "maxPos" ? value : maxPos,
        max_leverage: key === "leverage" ? value : leverage,
      });
    }, 600); // 600ms debounce prevents API spam while dragging
  };

  const handleToggleSwitch = (switchKey) => {
    setKillSwitches(prev => {
      const next = prev.map(ks => ks.key === switchKey ? { ...ks, active: !ks.active } : ks);
      const switchPayload = next.reduce((acc, ks) => ({ ...acc, [ks.key]: ks.active }), {});
      saveRiskConfig({ kill_switches: switchPayload });
      return next;
    });
  };

  const handleStrategyAllocationChange = (id, value) => {
    const numeric = Math.max(0, Math.min(100, Number(value)));
    setStrategyLimits(prev => prev.map(s => s.id === id ? { ...s, allocationPct: numeric } : s));

    clearTimeout(strategyDebounceMapRef.current[id]);
    strategyDebounceMapRef.current[id] = setTimeout(async () => {
      try {
        await endpoints.risk.updateStrategyLimit(id, { capital_limit_pct: numeric });
        setToast({ type: "success", msg: "Strategy allocation updated." });
      } catch (err) {
        setToast({ type: "error", msg: "Failed to update strategy limit." });
      }
    }, 600);
  };

  const Toggle = ({ active, onClick }) => (
    <div onClick={onClick} style={{
      width: 38, height: 20, borderRadius: 99, cursor: "pointer", position: "relative", transition: "all 0.2s",
      background: active ? `${C.green}30` : C.bg3, border: `1px solid ${active ? `${C.green}80` : C.border}`, flexShrink: 0
    }}>
      <div style={{
        position: "absolute", top: 2, left: active ? 20 : 2, width: 14, height: 14, borderRadius: "50%", transition: "all 0.2s",
        background: active ? C.green : C.t3, boxShadow: active ? `0 0 8px ${C.green}` : "none"
      }} />
    </div>
  );

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1, position: "relative" }}>
      <SectionH title="Risk Settings" sub="Configure kill switches and position limits to protect your capital" />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {/* Left Column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <Card cls="p-5">
            <PanelTitle title="Global Risk Limits" sub="Account-wide exposure ceilings" />
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {[
                { k: "maxLoss", l: "Max Daily Loss", v: maxLoss, min: 100, max: 5000, step: 50, prefix: "$", c: C.red },
                { k: "maxPos", l: "Max Open Positions", v: maxPos, min: 1, max: 50, step: 1, prefix: "", c: C.orange },
                { k: "leverage", l: "Max Leverage", v: leverage, min: 1, max: 20, step: 1, prefix: "", suffix: "x", c: C.purple },
              ].map(ctrl => (
                <div key={ctrl.l}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                    <label style={{ color: C.t1, fontSize: 11, fontWeight: 700 }}>{ctrl.l}{isLoadingRisk ? " ..." : ""}</label>
                    <span style={{ color: ctrl.c, fontFamily: "monospace", fontSize: 13, fontWeight: 900 }}>{ctrl.prefix}{ctrl.v}{ctrl.suffix || ""}</span>
                  </div>
                  <input type="range" min={ctrl.min} max={ctrl.max} step={ctrl.step} value={ctrl.v} onChange={e => handleSliderChange(ctrl.k, +e.target.value)} style={{ width: "100%", accentColor: ctrl.c }} />
                </div>
              ))}
            </div>
          </Card>

          <Card cls="p-5">
            <PanelTitle title="Active Kill Switches" sub="Automated trading halts" />
            <div style={{ display: "flex", flexDirection: "column" }}>
              {killSwitches.map((ks, i) => (
                <div key={ks.key} style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 0", borderBottom: i === killSwitches.length - 1 ? "none" : `1px solid ${C.border}` }}>
                  <span style={{ color: ks.active ? C.t1 : C.t3, fontSize: 11, fontFamily: "monospace", flex: 1, transition: "color 0.2s" }}>{ks.l}</span>
                  <Toggle active={ks.active} onClick={() => handleToggleSwitch(ks.key)} />
                </div>
              ))}
            </div>
          </Card>
        </div>

        {/* Right Column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <Card cls="p-5">
            <PanelTitle title="Position Limits" sub="Per-strategy capital allocation maximums" />
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {isLoadingRisk ? <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>Loading limits...</div> : strategyLimits.length === 0 ? <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>No active strategies deployed.</div> : strategyLimits.map(s => (
                <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ color: C.t1, fontSize: 11, fontWeight: 700, marginBottom: 6 }}>{s.name}</div>
                    <input type="range" min={0} max={100} step={1} value={s.allocationPct} onChange={(e) => handleStrategyAllocationChange(s.id, e.target.value)} style={{ width: "100%", accentColor: C.cyan }} />
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", width: 50 }}>
                    <span style={{ color: C.cyan, fontSize: 12, fontWeight: 900, fontFamily: "monospace" }}>{s.allocationPct}%</span>
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card cls="p-5">
            <PanelTitle title="Margin Safety" sub="Live exchange margin health" />
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {marginData.map(m => (
                <div key={m.l}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                    <span style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>{m.l}</span>
                    <span style={{ color: m.c, fontSize: 11, fontFamily: "monospace", fontWeight: 700 }}>{m.v}%</span>
                  </div>
                  <ProgressBar v={m.v} max={100} color={m.c} h={5} />
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>

      {toast && (
        <div style={{ position: "fixed", right: 24, bottom: 24, background: toast.type === "success" ? `${C.green}20` : `${C.red}20`, border: `1px solid ${toast.type === "success" ? `${C.green}55` : `${C.red}55`}`, color: toast.type === "success" ? C.green : C.red, borderRadius: 8, padding: "10px 16px", fontSize: 11, fontFamily: "monospace", fontWeight: 700, zIndex: 120, boxShadow: "0 10px 30px rgba(0,0,0,0.5)" }}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  PAGE: BILLING & SUBSCRIPTIONS
// ═══════════════════════════════════════════════════════════════════
function Billing() {
  const [currentPlan, setCurrentPlan] = useState(null);
  const [billingHistory, setBillingHistory] = useState([]);
  const [savedMethods, setSavedMethods] = useState([]);
  const [currency, setCurrency] = useState("USD");
  const [isUpgradeModalOpen, setIsUpgradeModalOpen] = useState(false);
  const [isLoadingBilling, setIsLoadingBilling] = useState(true);
  const [isCheckoutLoading, setIsCheckoutLoading] = useState("");
  const [billingError, setBillingError] = useState("");

  const plans = [
    { id: "free", name: "Free", usd: 0, inr: 0, features: ["Algorithm Builder", "3 Backtests/mo", "No Deployment"], description: "Start building and testing core ideas." },
    { id: "starter", name: "Starter", usd: 5, inr: 399, features: ["2 Deployed Algos", "Unlimited Backtesting", "Algorithm Indicators"], description: "Launch your first production algos." },
    { id: "pro", name: "Pro", usd: 12, inr: 999, features: ["5 Deployed Algos", "Unlimited Backtesting", "Algorithm Indicators"], description: "Built for active algo developers." },
    { id: "enterprise", name: "Enterprise", usd: 24, inr: 1999, features: ["8 Deployed Algos", "3 ML/DL Models Training", "Algorithm Indicators"], description: "Institutional-grade execution and controls." },
  ];
  const EXTRA_ML_MODEL_PRICE = { INR: 199, USD: 2.5 };

  useEffect(() => {
    const controller = new AbortController();
    const loadBilling = async () => {
      setIsLoadingBilling(true);
      setBillingError("");
      try {
        const [planRes, invoicesRes, methodsRes] = await Promise.allSettled([
          endpoints.billing.getPlan(),
          endpoints.billing.getInvoices(),
          endpoints.billing.getPaymentMethods()
        ]);

        if (planRes.status === 'fulfilled' && planRes.value.data) {
          setCurrentPlan(planRes.value.data.plan || planRes.value.data);
        }
        if (invoicesRes.status === 'fulfilled' && invoicesRes.value.data) {
          setBillingHistory(invoicesRes.value.data.invoices || []);
        }
        if (methodsRes.status === 'fulfilled' && methodsRes.value.data) {
          setSavedMethods(methodsRes.value.data.methods || []);
        }
      } catch (err) {
        console.error("Failed loading billing data:", err);
        setBillingError("Unable to sync billing profile with the server.");
      } finally {
        setIsLoadingBilling(false);
      }
    };
    loadBilling();
    return () => controller.abort();
  }, []);

  const handleCheckout = async (planId) => {
    if (isCheckoutLoading) return;
    setBillingError("");
    setIsCheckoutLoading(planId);
    try {
      let tier = "free";
      if (planId === "pro") tier = "pro_999";
      else if (planId === "enterprise") tier = "elite_1999";
      else if (planId === "starter") tier = "pro_999"; // Fallback to pro if starter selected

      const data = await endpoints.billing.createCheckout({ tier, currency });
      if (data && data.checkoutUrl) {
        window.location.href = data.checkoutUrl; // Redirect to Stripe/Razorpay
      } else {
        throw new Error("Payment gateway URL not provided by backend.");
      }
    } catch (err) {
      Sentry.captureException(err, {
        tags: { type: "checkout_error", planId },
        extra: { currency, planId }
      });
      setBillingError(err?.response?.data?.detail || "Checkout initialization failed.");
    } finally {
      setIsCheckoutLoading("");
    }
  };

  const money = (amountUSD, amountINR) => {
    return currency === "INR" ? `₹${Number(amountINR || 0).toLocaleString()}` : `$${Number(amountUSD || 0).toLocaleString()}`;
  };

  const fmtDate = (d) => {
    if (!d) return "N/A";
    const dt = new Date(d);
    return isNaN(dt.getTime()) ? d : dt.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  };

  const defaultMethod = savedMethods.find((m) => m.isDefault) || savedMethods[0];

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1, position: "relative" }}>
      <SectionH title="Subscription & Billing" sub="Manage your plan, payment methods, and invoices" />

      {!!billingError && (
        <div style={{ marginBottom: 16, background: `${C.red}12`, border: `1px solid ${C.red}44`, color: C.red, borderRadius: 8, padding: "10px 14px", fontSize: 11, fontFamily: "monospace", fontWeight: 700 }}>
          <AlertTriangle size={12} style={{ display: "inline", marginRight: 6 }} /> {billingError}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
        {/* Current Plan Card */}
        <Card cls="p-6" style={{ border: `1px solid ${C.cyan}40` }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
            <div>
              <Tag2 c="cyan">CURRENT PLAN</Tag2>
              <h2 style={{ color: C.t1, fontWeight: 900, fontSize: 22, marginTop: 6, textTransform: "capitalize" }}>
                {isLoadingBilling ? "Loading..." : `${currentPlan?.name || "Free"} Tier`}
              </h2>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ color: C.cyan, fontSize: 28, fontWeight: 900 }}>
                {isLoadingBilling ? "..." : money(currentPlan?.priceUSD, currentPlan?.priceINR)}
              </div>
              <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>/month</div>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 16 }}>
            {(isLoadingBilling ? ["Syncing features..."] : (currentPlan?.features || [])).map(f => (
              <div key={f} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <CheckCircle size={10} style={{ color: C.cyan, flexShrink: 0 }} />
                <span style={{ color: C.t2, fontSize: 10, fontFamily: "monospace" }}>{f}</span>
              </div>
            ))}
          </div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: C.bg3, borderRadius: 8, padding: "10px 14px", marginBottom: 14 }}>
            <span style={{ color: C.t2, fontSize: 11, fontFamily: "monospace" }}>
              Next billing: <span style={{ color: C.t1, fontWeight: 700 }}>{isLoadingBilling ? "..." : fmtDate(currentPlan?.nextBillingDate)}</span>
            </span>
            <Tag2 c={currentPlan?.autoRenew ? "green" : "red"}>{currentPlan?.autoRenew ? "AUTO-RENEW ON" : "AUTO-RENEW OFF"}</Tag2>
          </div>
          <Btn v="outline" sz="sm" cls="w-full justify-center" onClick={() => setIsUpgradeModalOpen(true)}>Upgrade Plan</Btn>
        </Card>

        {/* Payment Methods & History */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Card cls="p-6">
            <PanelTitle title="Payment Method" />
            {isLoadingBilling ? (
              <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>Loading vault...</div>
            ) : !defaultMethod ? (
              <div style={{ background: C.bg3, border: `1px dashed ${C.border}`, borderRadius: 10, padding: 16, color: C.t3, fontSize: 10, fontFamily: "monospace", textAlign: "center" }}>No payment methods on file.</div>
            ) : (
              <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10, padding: 14, display: "flex", alignItems: "center", gap: 12 }}>
                <div style={{ background: "linear-gradient(135deg,#1a1f71,#003087)", borderRadius: 6, padding: "6px 10px", flexShrink: 0 }}>
                  <CreditCard size={18} style={{ color: "#fff" }} />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ color: C.t1, fontWeight: 700, fontSize: 12 }}>{defaultMethod.brand} •••• {defaultMethod.last4}</div>
                  <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>Expires {defaultMethod.expiry}</div>
                </div>
                {defaultMethod.isDefault && <Tag2 c="green">DEFAULT</Tag2>}
              </div>
            )}
            <Btn v="ghost" sz="sm" Icon={Plus} cls="w-full justify-center mt-3">Add Payment Method</Btn>
          </Card>

          <Card cls="p-6 flex-1">
            <PanelTitle title="Billing History" />
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                  {["Invoice", "Date", "Amount", "Status"].map(h => <th key={h} style={{ color: C.t3, fontWeight: 900, padding: "6px 8px", textAlign: "left", fontSize: 8, letterSpacing: 2, textTransform: "uppercase" }}>{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {isLoadingBilling ? (
                  <tr><td colSpan={4} style={{ padding: "12px 8px", color: C.t3, textAlign: "center" }}>Loading ledger...</td></tr>
                ) : billingHistory.length === 0 ? (
                  <tr><td colSpan={4} style={{ padding: "12px 8px", color: C.t3, textAlign: "center" }}>No invoices found.</td></tr>
                ) : billingHistory.map(inv => (
                  <tr key={inv.id} style={{ borderBottom: `1px solid ${C.border}20` }}>
                    <td style={{ padding: "8px", color: C.cyan, cursor: "pointer" }}>{inv.id}</td>
                    <td style={{ padding: "8px", color: C.t2 }}>{fmtDate(inv.date)}</td>
                    <td style={{ padding: "8px", color: C.t1, fontWeight: 700 }}>{money(inv.amtUSD, inv.amtINR)}</td>
                    <td style={{ padding: "8px" }}><Tag2 c={inv.status === "paid" ? "green" : inv.status === "pending" ? "orange" : "red"}>{inv.status}</Tag2></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      </div>

      {/* Upgrade Modal */}
      {isUpgradeModalOpen && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(1,6,8,0.85)", backdropFilter: "blur(8px)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
          <div style={{ width: "min(1200px, 95vw)", maxHeight: "90vh", overflowY: "auto", background: C.bg1, border: `1px solid ${C.border}`, borderRadius: 16, padding: 32, boxShadow: "0 40px 100px rgba(0,0,0,0.8)" }}>

            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 24 }}>
              <div>
                <h3 style={{ color: C.t1, fontSize: 24, fontWeight: 900 }}>Upgrade Your Plan</h3>
                <p style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", marginTop: 6 }}>High-performance plans for quant-scale execution.</p>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
                <div style={{ display: "flex", background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 999, padding: 4 }}>
                  <button onClick={() => setCurrency("USD")} style={{ background: currency === "USD" ? `${C.cyan}20` : "transparent", color: currency === "USD" ? C.cyan : C.t2, border: `1px solid ${currency === "USD" ? `${C.cyan}55` : "transparent"}`, borderRadius: 999, padding: "6px 14px", fontSize: 10, fontFamily: "monospace", fontWeight: 700, cursor: "pointer", transition: "all 0.2s" }}>USD ($)</button>
                  <button onClick={() => setCurrency("INR")} style={{ background: currency === "INR" ? `${C.cyan}20` : "transparent", color: currency === "INR" ? C.cyan : C.t2, border: `1px solid ${currency === "INR" ? `${C.cyan}55` : "transparent"}`, borderRadius: 999, padding: "6px 14px", fontSize: 10, fontFamily: "monospace", fontWeight: 700, cursor: "pointer", transition: "all 0.2s" }}>INR (₹)</button>
                </div>
                <button onClick={() => setIsUpgradeModalOpen(false)} style={{ color: C.t3, background: "transparent", border: "none", cursor: "pointer" }} className="hover:text-red-400"><XCircle size={24} /></button>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-5">
              {plans.map((p) => {
                const isActive = currentPlan?.id === p.id;
                const isProcessingThis = isCheckoutLoading === p.id;
                const priceValue = currency === "INR" ? p.inr : p.usd;
                const extraMlValue = EXTRA_ML_MODEL_PRICE[currency];

                return (
                  <div key={p.id} style={{ background: C.bg2, border: `1px solid ${p.recommended || isActive ? `${C.cyan}60` : C.border}`, borderRadius: 14, padding: 24, boxShadow: p.recommended ? `0 0 30px ${C.cyan}15` : "none", position: "relative", display: "flex", flexDirection: "column" }}>
                    {p.recommended && <div style={{ position: "absolute", top: 12, right: 12 }}><Tag2 c="cyan">RECOMMENDED</Tag2></div>}
                    {isActive && <div style={{ position: "absolute", top: 12, right: 12 }}><Tag2 c="green">CURRENT</Tag2></div>}

                    <div style={{ color: C.t1, fontWeight: 900, fontSize: 20, marginBottom: 6 }}>{p.name}</div>
                    <div style={{ color: C.t3, fontSize: 10, fontFamily: "monospace", marginBottom: 16, minHeight: 30 }}>{p.description}</div>

                    <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginBottom: 20 }}>
                      <span style={{ color: C.cyan, fontWeight: 900, fontSize: 36, lineHeight: 1 }}>{currency === "INR" ? "₹" : "$"}{priceValue.toLocaleString()}</span>
                      <span style={{ color: C.t3, fontSize: 10, fontFamily: "monospace" }}>/month</span>
                    </div>

                    <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 24, flex: 1 }}>
                      {p.features.map(f => (
                        <div key={f} style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                          <CheckCircle size={12} style={{ color: C.cyan, flexShrink: 0, marginTop: 2 }} />
                          <span style={{ color: C.t2, fontSize: 11, fontFamily: "monospace", lineHeight: 1.4 }}>{f}</span>
                        </div>
                      ))}
                      {p.id === "enterprise" && (
                        <div style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", marginTop: 4, paddingLeft: 20 }}>
                          +{currency === "INR" ? "₹" : "$"}{extraMlValue} per extra ML model
                        </div>
                      )}
                    </div>

                    <button
                      disabled={!!isCheckoutLoading || isActive}
                      onClick={() => handleCheckout(p.id)}
                      style={{
                        width: "100%", background: isActive ? `${C.green}20` : C.cyan, color: isActive ? C.green : "#000",
                        border: `1px solid ${isActive ? `${C.green}40` : "transparent"}`, borderRadius: 10, padding: "12px",
                        fontSize: 12, fontFamily: "monospace", fontWeight: 900, textTransform: "uppercase", letterSpacing: 1,
                        cursor: !!isCheckoutLoading || isActive ? "not-allowed" : "pointer",
                        opacity: !!isCheckoutLoading && !isProcessingThis ? 0.4 : 1,
                        transition: "all 0.2s"
                      }}
                    >
                      {isActive ? "Current Plan" : isProcessingThis ? "Redirecting..." : "Subscribe"}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  PAGE: SECURITY LOGS
// ═════════════════════════════════════════════════════════════════
function SecurityLogs() {
  const [logs, setLogs] = useState([]);
  const [summary, setSummary] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");

  useEffect(() => {
    const fetchSecurityData = async () => {
      try {
        // SECURITY ENDPOINT QUARANTINED - Admin/God mode panel only
        // This data will be loaded when the admin panel is implemented
        setSummary({ logins_30d: 0, api_calls_24h: 0, failed_attempts: 0, active_sessions: 0 });
        setLogs([]);
      } catch (err) {
        console.error("Failed to fetch security logs:", err);
      } finally {
        setIsLoading(false);
      }
    };
    fetchSecurityData();
  }, []);

  const filteredLogs = logs.filter(log => {
    const term = searchTerm.toLowerCase();
    return (
      log.event.toLowerCase().includes(term) ||
      log.ip.toLowerCase().includes(term) ||
      log.loc.toLowerCase().includes(term) ||
      log.device.toLowerCase().includes(term)
    );
  });

  const handleExport = () => {
    const headers = ["Event", "IP Address", "Location", "Device", "Time", "Status"];
    const csvContent = [
      headers.join(","),
      ...filteredLogs.map(l => `"${l.event}","${l.ip}","${l.loc}","${l.device}","${l.time}","${l.status}"`)
    ].join("\n");

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `security_logs_${new Date().toISOString().split('T')[0]}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      <SectionH title="Security Logs" sub="All authentication events and API key access records" />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, marginBottom: 14 }}>
        {[
          { l: "Logins (30d)", v: isLoading ? "..." : summary?.logins_30d || 0, c: C.cyan },
          { l: "API Calls (24h)", v: isLoading ? "..." : summary?.api_calls_24h?.toLocaleString() || 0, c: C.purple },
          { l: "Failed Attempts", v: isLoading ? "..." : summary?.failed_attempts || 0, c: C.red },
          { l: "Active Sessions", v: isLoading ? "..." : summary?.active_sessions || 0, c: C.green },
        ].map(s => (
          <Card key={s.l} cls="p-4">
            <div style={{ color: C.t3, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase", marginBottom: 4 }}>{s.l}</div>
            <div style={{ color: s.c, fontSize: 18, fontWeight: 900, fontFamily: "monospace" }}>{s.v}</div>
          </Card>
        ))}
      </div>

      <Card>
        <div style={{ padding: "14px 16px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "center", gap: 10 }}>
          <Search size={13} style={{ color: C.t3 }} />
          <input
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search events, IPs, locations..."
            style={{ background: "transparent", border: "none", outline: "none", color: C.t1, fontSize: 11, fontFamily: "monospace", flex: 1 }}
            className="placeholder:text-slate-700"
          />
          <Btn v="outline" sz="xs" Icon={Download} onClick={handleExport} disabled={filteredLogs.length === 0}>Export</Btn>
        </div>

        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              {["Event", "IP Address", "Location", "Device", "Time", "Status"].map(h => (
                <th key={h} style={{ color: C.t3, fontWeight: 900, padding: "10px 14px", textAlign: "left", fontSize: 8, letterSpacing: 2, textTransform: "uppercase" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={6} style={{ padding: "20px", textAlign: "center", color: C.t3 }}>Loading security data...</td>
              </tr>
            ) : filteredLogs.length === 0 ? (
              <tr>
                <td colSpan={6} style={{ padding: "20px", textAlign: "center", color: C.t3 }}>No security logs match your search.</td>
              </tr>
            ) : (
              filteredLogs.map(log => (
                <tr key={log.id} style={{ borderBottom: `1px solid ${C.border}15` }} className="hover:bg-white/5 transition-colors">
                  <td style={{ padding: "9px 14px", color: log.status.toLowerCase() === "failed" ? C.red : C.t1, fontWeight: 700 }}>{log.event}</td>
                  <td style={{ padding: "9px 14px", color: C.t2, fontFamily: "monospace" }}>{log.ip}</td>
                  <td style={{ padding: "9px 14px", color: C.t2 }}>{log.loc}</td>
                  <td style={{ padding: "9px 14px", color: C.t3, fontSize: 9 }}>{log.device}</td>
                  <td style={{ padding: "9px 14px", color: C.t3 }}>{log.time}</td>
                  <td style={{ padding: "9px 14px" }}>
                    <Tag2 c={log.status.toLowerCase() === "success" ? "green" : "red"}>{log.status.toUpperCase()}</Tag2>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  PAGE: REFERRAL
// ═══════════════════════════════════════════════════════════════════
function Referral() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState(null);

  useEffect(() => {
    const controller = new AbortController();

    const fetchReferralStats = async () => {
      try {
        const data = await endpoints.user.getReferralStats();
        setStats(data);
      } catch (err) {
        if (err?.name !== "CanceledError") {
          console.error("Failed to load referral stats:", err);
        }
      } finally {
        setLoading(false);
      }
    };

    fetchReferralStats();
    return () => controller.abort();
  }, []);

  // Clear toast after 2.5 seconds
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 2500);
    return () => clearTimeout(timer);
  }, [toast]);

  const handleCopy = async () => {
    if (!stats?.referral_link) return;
    try {
      await navigator.clipboard.writeText(stats.referral_link);
      setToast({ type: "success", msg: "Referral link copied to clipboard!" });
    } catch (err) {
      setToast({ type: "error", msg: "Failed to copy link." });
    }
  };

  const handleTwitterShare = () => {
    if (!stats?.referral_link) return;
    const text = encodeURIComponent("Trade at the speed of alpha with VyomQuant's institutional quant terminal. Join here: ");
    window.open(`https://twitter.com/intent/tweet?text=${text}&url=${encodeURIComponent(stats.referral_link)}`, '_blank');
  };

  const handleEmailShare = () => {
    if (!stats?.referral_link) return;
    const subject = encodeURIComponent("Invitation: VyomQuant Quant Terminal");
    const body = encodeURIComponent(`I've been using VyomQuant to automate my trading strategies. You can get a free trial using my institutional referral link here:\n\n${stats.referral_link}`);
    window.location.href = `mailto:?subject=${subject}&body=${body}`;
  };

  const displayLink = loading ? "Loading secure link..." : (stats?.referral_link || "No link generated yet.");

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1, position: "relative" }}>
      <SectionH title="Referral Program" sub="Earn 20% lifetime commission on every referred subscription" />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 20 }}>
        {[
          { l: "Total Referrals", v: loading ? "..." : stats?.total_referrals || 0, c: C.cyan, I: Users },
          { l: "Active Subs", v: loading ? "..." : stats?.active_subs || 0, c: C.green, I: CheckCircle },
          { l: "Total Earned", v: loading ? "..." : `$${(stats?.total_earned || 0).toLocaleString()}`, c: C.gold, I: DollarSign },
          { l: "Pending Payout", v: loading ? "..." : `$${(stats?.pending_payout || 0).toLocaleString()}`, c: C.orange, I: Clock }
        ].map(s => (
          <Card key={s.l} cls="p-4">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
              <span style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", letterSpacing: 2, textTransform: "uppercase" }}>{s.l}</span>
              <s.I size={12} style={{ color: s.c }} />
            </div>
            <div style={{ color: s.c, fontSize: 20, fontWeight: 900 }}>{s.v}</div>
          </Card>
        ))}
      </div>

      <Card cls="p-6 mb-12">
        <PanelTitle title="Your Referral Link" />
        <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 16px", display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          <Link2 size={14} style={{ color: C.cyan, flexShrink: 0 }} />
          <code style={{ color: loading ? C.t3 : C.cyan, fontSize: 11, fontFamily: "monospace", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {displayLink}
          </code>
          <Btn v="outline" sz="xs" Icon={Copy} onClick={handleCopy} disabled={loading || !stats?.referral_link}>Copy</Btn>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Btn v="outline" sz="sm" Icon={Send} onClick={handleTwitterShare} disabled={loading || !stats?.referral_link}>Share on Twitter</Btn>
          <Btn v="outline" sz="sm" Icon={Mail} onClick={handleEmailShare} disabled={loading || !stats?.referral_link}>Share via Email</Btn>
        </div>
      </Card>

      {/* Toast Notification */}
      {toast && (
        <div style={{ position: "fixed", right: 24, bottom: 24, background: toast.type === "success" ? `${C.green}20` : `${C.red}20`, border: `1px solid ${toast.type === "success" ? `${C.green}55` : `${C.red}55`}`, color: toast.type === "success" ? C.green : C.red, borderRadius: 8, padding: "10px 16px", fontSize: 11, fontFamily: "monospace", fontWeight: 700, zIndex: 120, boxShadow: "0 10px 30px rgba(0,0,0,0.5)" }}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  PAGE: PROFILE
// ═══════════════════════════════════════════════════════════════════

function Profile() {
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchProfile = async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await endpoints.user.getProfile() || {};
        setProfile(data);
      } catch (err) {
        console.error("Failed to load profile:", err);
        if (err?.response?.status === 401 || err?.response?.status === 403) {
          setError("Authentication required. Please log in.");
        } else {
          setError("Failed to load profile data");
        }
        setProfile({});
      } finally {
        setLoading(false);
      }
    };
    fetchProfile();
  }, []);

  if (loading) {
    return (
      <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: C.t2, fontFamily: "monospace" }}>
          Loading profile...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: C.red, fontFamily: "monospace", textAlign: "center" }}>
          {error}
        </div>
      </div>
    );
  }

  // Render specific fields: id, email, role
  const fields = [
    { key: 'id', label: 'User ID' },
    { key: 'email', label: 'Email' },
    { key: 'role', label: 'Role' },
  ];

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      <SectionH title="User Profile" sub="Your account information" />
      <Card cls="p-6">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))", gap: 16 }}>
          {fields.map(({ key, label }) => (
            <div key={key} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <label style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 900, letterSpacing: 2, textTransform: "uppercase" }}>
                {label}
              </label>
              <input
                value={profile?.[key] !== null && profile?.[key] !== undefined ? String(profile[key]) : ""}
                readOnly
                style={{
                  background: C.bg3,
                  border: `1px solid ${C.border}`,
                  borderRadius: 8,
                  padding: "10px 12px",
                  color: C.t1,
                  fontSize: 11,
                  fontFamily: "monospace",
                  outline: "none"
                }}
              />
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  MAIN APP COMPONENT
// ═══════════════════════════════════════════════════════════════════


// ═══════════════════════════════════════════════════════════════════════
//  PAGE: LEADERBOARD
// ═════════════════════════════════════════════════════════════════════════
function Leaderboard() {
  const [leaderboardData, setLeaderboardData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeFilter, setActiveFilter] = useState("30d");

  const filters = [
    { label: "All Time", value: "all" },
    { label: "30 Days", value: "30d" },
    { label: "7 Days", value: "7d" },
    { label: "Today", value: "today" }
  ];

  useEffect(() => {
    const controller = new AbortController();
    const fetchLeaderboard = async () => {
      setLoading(true);
      try {
        const data = await endpoints.leaderboard.getLeaderboard(activeFilter);
        const rows = Array.isArray(data) ? data : data?.leaderboard || data?.data || [];
        setLeaderboardData(rows);
      } catch (err) {
        if (err?.name !== "CanceledError") console.error("Failed to load leaderboard:", err);
      } finally {
        setLoading(false);
      }
    };
    fetchLeaderboard();
    return () => controller.abort();
  }, [activeFilter]);

  return (
    <div style={{ padding: 20, overflowY: "auto", flex: 1 }}>
      <SectionH title="Performance Leaderboard" sub="Top-performing public strategies this month" />
      <Card>
        <div style={{ padding: "14px 16px", borderBottom: `1px solid ${C.border}`, display: "flex", gap: 8 }}>
          {filters.map((f) => (
            <button
              key={f.value}
              onClick={() => setActiveFilter(f.value)}
              style={{
                background: activeFilter === f.value ? C.cyan + "15" : "transparent",
                color: activeFilter === f.value ? C.cyan : C.t3,
                border: `1px solid ${activeFilter === f.value ? C.cyan + "40" : C.border}`,
                borderRadius: 5, padding: "4px 10px", fontSize: 9, fontFamily: "monospace",
                cursor: "pointer", fontWeight: activeFilter === f.value ? 900 : 400,
                transition: "all 0.2s"
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 10, fontFamily: "monospace" }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${C.border}` }}>
              {["Rank", "Operator", "Strategy", "P&L", "Max DD", "Trades", "Plan"].map(h => <th key={h} style={{ color: C.t3, fontWeight: 900, padding: "10px 14px", textAlign: "left", fontSize: 8, letterSpacing: 2, textTransform: "uppercase" }}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} style={{ padding: "30px", textAlign: "center", color: C.t3 }}>Loading rankings...</td></tr>
            ) : leaderboardData.length === 0 ? (
              <tr><td colSpan={7} style={{ padding: "30px", textAlign: "center", color: C.t3 }}>No ranking data available.</td></tr>
            ) : (
              leaderboardData.map(r => (
                <tr key={r.rank} style={{ borderBottom: `1px solid ${C.border}15`, background: r.rank <= 3 ? `rgba(0,212,255,0.02)` : undefined }} className="hover:bg-white/5 transition-colors">
                  <td style={{ padding: "10px 14px" }}><span style={{ color: r.rank === 1 ? C.gold : r.rank === 2 ? "#c0c0c0" : r.rank === 3 ? "#cd7f32" : C.t3, fontWeight: 900, fontSize: r.rank <= 3 ? 14 : 10 }}>{r.rank === 1 ? "🥇" : r.rank === 2 ? "🥈" : r.rank === 3 ? "🥉" : `#${r.rank}`}</span></td>
                  <td style={{ padding: "10px 14px", color: C.t1, fontWeight: 700 }}>{r.name}</td>
                  <td style={{ padding: "10px 14px", color: C.t2 }}>{r.strat}</td>
                  <td style={{ padding: "10px 14px", color: r.pnl >= 0 ? C.green : C.red, fontWeight: 900 }}>{r.pnl >= 0 ? "+" : ""}{r.pnl}%</td>
                  <td style={{ padding: "10px 14px", color: C.red }}>-{r.dd}%</td>
                  <td style={{ padding: "10px 14px", color: C.t2 }}>{Number(r.trades).toLocaleString()}</td>
                  <td style={{ padding: "10px 14px" }}><Tag2 c={r.sub === "Institutional" ? "gold" : r.sub === "Pro" ? "cyan" : "green"}>{r.sub?.toUpperCase()}</Tag2></td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
//  UPDATE PASSWORD PAGE (STEP 3.2 Private Beta Blocker)
// ═══════════════════════════════════════════════════════════════════
function UpdatePasswordPage({ go }) {
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
      const { error } = await supabase.auth.updateUser({ password });
      
      if (error) throw error;
      
      setSuccess(true);
      setTimeout(() => go("dashboard"), 2000);
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
            Password updated! Redirecting...
          </div>
        ) : (
          <form onSubmit={handleUpdate} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <Inp
              lbl="New Password"
              ph="•••••••••••"
              type="password"
              icon={Lock}
              val={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={loading}
            />
            <Btn v="primary" sz="md" cls="w-full justify-center mt-2" disabled={loading}>
              {loading ? "Updating..." : "Update Password"}
            </Btn>
            {!!error && <div style={{ marginTop: 10, color: C.red, fontSize: 12, textAlign: "center" }}>{error}</div>}
          </form>
        )}
      </div>
    </div>
  );
}

// ==============================
//  MASTER APP ROUTER & LAYOUT SHELL
// ==============================
function App() {
  const [page, setPage] = useState(sessionStorage.getItem("token") ? "dashboard" : "landing");
  const [resumeBuilderStrategy, setResumeBuilderStrategy] = useState(null);
  const [activeBacktestStrategy, setActiveBacktestStrategy] = useState(null);
  const tenantId = "default"; // Default tenant ID for bot monitoring

  // Toast notification state
  const [toasts, setToasts] = useState([]);
  const addToast = useCallback((type, message) => {
    const id = crypto.randomUUID();
    setToasts(prev => [...prev, { id, type, message }]);
    return id;
  }, []);
  const removeToast = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  // Expose toast helpers globally for non-React contexts
  useEffect(() => {
    window.showToast = addToast;
    return () => { delete window.showToast; };
  }, [addToast]);

  const go = (targetPage) => setPage(targetPage);

  useEffect(() => {
    const handleNavigate = (e) => { if (e.detail) go(e.detail); };
    window.addEventListener('navigate', handleNavigate);

    const handleAuthExpired = () => {
      sessionStorage.removeItem("token");
      setPage("landing");
    };

    // Password Recovery Interceptor
    const hash = window.location.hash;
    if (hash && hash.includes("type=recovery")) {
      const params = new URLSearchParams(hash.substring(1));
      const token = params.get("access_token");
      if (token) {
        sessionStorage.setItem("token", token);
        setPage("reset-password");
        window.history.replaceState(null, "", window.location.pathname);
      }
    }

    // Sync auth across tabs
    const handleStorageChange = (e) => {
      if (e.key === "token") {
        if (!e.newValue) {
          // Token removed in another tab
          setPage("landing");
        } else if (e.newValue && page === "landing") {
          // Token added in another tab
          setPage("dashboard");
        }
      }
    };

    window.addEventListener('auth-expired', handleAuthExpired);
    window.addEventListener('storage', handleStorageChange);

    return () => {
      window.removeEventListener('navigate', handleNavigate);
      window.removeEventListener('auth-expired', handleAuthExpired);
      window.removeEventListener('storage', handleStorageChange);
    };
  }, [page]);

  if (page === "landing") return <Landing go={go} />;
  if (page === "auth-signin") return <AuthPage mode="signin" go={go} />;
  if (page === "auth-signup") return <AuthPage mode="signup" go={go} />;
  if (page === "reset-password") return <UpdatePasswordPage go={go} />;
  if (page === "2fa") return <TwoFA go={go} />;
  if (page === "wizard") return <Wizard go={go} />;
  if (page === "legal") return <div style={{ background: C.bg, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", width: "100%" }}><LegalPage onBack={() => { const token = sessionStorage.getItem("token"); if (token) go("dashboard"); else go("landing"); }} go={go} /></div>;

  // Fallback redirect for disabled Live Trading page
  if (page === "trading") {
    go("dashboard");
    return null;
  }

  // Infrastructure Ops admin-only - redirect client users to dashboard
  if (page === "infra-ops") {
    go("dashboard");
    return null;
  }

  const PAGES = {
    dashboard: <Dashboard go={go} />,
    "bot-monitor": <BotMonitoringConsole wsClient={wsClient} accountId={tenantId} />,
    "signal-trace": <SignalTraceVisualization wsClient={wsClient} accountId={tenantId} />,
    // Infrastructure Ops admin-only (accessible via direct URL: /admin/infra)
    // Terminal removed - not applicable for algo-only platform
    // Live Trading disabled - trading: <LiveTrading />,
    builder: <StrategyBuilder onBack={() => go("strategies")} onBacktest={(payload) => { setActiveBacktestStrategy(payload); go("backtest"); }} />,
    strategies: <Strategies go={go} resumeBuilderStrategy={resumeBuilderStrategy} onResumeBuilderConsumed={() => setResumeBuilderStrategy(null)} />,
    backtest: <Backtester strategy={activeBacktestStrategy} onBack={() => { setResumeBuilderStrategy(activeBacktestStrategy); go("strategies"); }} />,
    // portfolio removed - manual trading feature not applicable
    // history removed - manual trading feature not applicable
    exchange: <ExchangeManager />,
    risk: <RiskSettings />,
    billing: <Billing />,
    leaderboard: <Leaderboard />,
    referral: <Referral />,
    profile: <Profile />,
    // Docs component not implemented - docs: <Docs/>,
    support: <SupportPage />,
    notifications: <NotificationsPage />,
    legal: <LegalPage onBack={() => { const token = sessionStorage.getItem("token"); if (token) go("dashboard"); else go("landing"); }} go={go} />,
  };

  return (
    <div style={{ background: C.bg0, minHeight: "100vh", display: "flex", fontFamily: "'IBM Plex Mono', 'Fira Code', monospace" }}>
      {/* Toast Notifications */}
      <ToastContainer toasts={toasts} onRemove={removeToast} />

      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&display=swap');
        
        /* Global Reset */
        * { 
          box-sizing: border-box; 
          margin: 0; 
          padding: 0; 
          -webkit-tap-highlight-color: transparent;
        }
      `}</style>
      <Sidebar page={page} go={go} />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, overflow: "hidden" }}>
        <TopBar go={go} />
        <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column" }}>
          {PAGES[page] || <div style={{ padding: 40, color: C.t2, fontFamily: "monospace" }}>Page not found: {page}</div>}
        </div>
      </div>
    </div>
  );
}

// ── Routing wrapper: separates public landing routes from the main app ──
// Public routes: /, /download, /admin/waitlist, /signup, /signin, /demo, /docs
// App route: everything else (authenticated terminal)
export default function AppWrapper() {
  return (
    <ErrorBoundary>
      <CopilotProvider>
        <Routes>
          {/* ── Public landing routes (new from ZIP integration) ── */}
          <Route
            path="/"
            element={
              <Suspense fallback={<div style={{ background: '#080A0E', minHeight: '100vh' }} />}>
                <LandingPage />
              </Suspense>
            }
          />
          <Route
            path="/download"
            element={
              <Suspense fallback={<div style={{ background: '#080A0E', minHeight: '100vh' }} />}>
                <DownloadPage />
              </Suspense>
            }
          />
          <Route
            path="/admin/waitlist"
            element={
              <Suspense fallback={<div style={{ background: '#080A0E', minHeight: '100vh' }} />}>
                <AdminDashboard />
              </Suspense>
            }
          />

          {/* ── Legal pages ── */}
          <Route
            path="/legal/privacy"
            element={
              <Suspense fallback={<div style={{ background: '#080A0E', minHeight: '100vh' }} />}>
                <LegalPageRoute type="privacy" />
              </Suspense>
            }
          />
          <Route
            path="/legal/terms"
            element={
              <Suspense fallback={<div style={{ background: '#080A0E', minHeight: '100vh' }} />}>
                <LegalPageRoute type="terms" />
              </Suspense>
            }
          />
          <Route
            path="/legal/risk"
            element={
              <Suspense fallback={<div style={{ background: '#080A0E', minHeight: '100vh' }} />}>
                <LegalPageRoute type="risk" />
              </Suspense>
            }
          />
          <Route
            path="/legal/refund"
            element={
              <Suspense fallback={<div style={{ background: '#080A0E', minHeight: '100vh' }} />}>
                <LegalPageRoute type="refund" />
              </Suspense>
            }
          />

          {/* ── Authenticated app — all other paths go to existing App ── */}
          {/* /app, /dashboard, /signup, /signin, /demo, and legacy direct access */}
          <Route
            path="/*"
            element={
              <LoadingProvider>
                <App />
              </LoadingProvider>
            }
          />
        </Routes>
      </CopilotProvider>
    </ErrorBoundary>
  );
}