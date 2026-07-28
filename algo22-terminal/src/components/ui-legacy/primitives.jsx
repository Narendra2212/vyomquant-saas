/**
 * ui-legacy/primitives.jsx
 *
 * Design system tokens, primitive UI components, and shared hooks
 * extracted from App.jsx (was ~lines 208-1488).
 *
 * DO NOT add page-level logic here. This file should only contain:
 *   - The C (theme colours/spacing) token object
 *   - Stateless and lightly-stateful micro-components
 *   - Shared hooks (usePolling)
 *   - Error/loading utilities
 */

import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  ArrowUpRight, XCircle, Bot, Info, Activity,
  AlertTriangle, RefreshCw, Zap
} from "lucide-react";

// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
//  DESIGN SYSTEM - PROFESSIONAL TRADING THEME
// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
export const C = {
  // Background hierarchy (reconciled with DESIGN_SYSTEM_V2)
  bg: "#080A0E",
  bg0: "#080A0E",
  bg1: "#0F1117",
  bg2: "#151821",
  bg3: "#1A202C",
  bg4: "#222C3A",

  // Border system (reconciled with DESIGN_SYSTEM_V2)
  border: "#1E2530",
  borderLight: "#2A3441",
  borderHover: "#3D4D5C",

  // Accent colors (cyan primary brand + blue accent)
  cyan: "#00D4FF",
  cyanL: "#67E8F9",
  cyanD: "#0083B0",
  cyanDim: "rgba(0, 212, 255, 0.15)",
  accent: "#00D4FF",
  accentHover: "#33E0FF",
  accentMuted: "#0083B0",
  blue: "#2962FF",

  // Profit / Loss (reconciled with DESIGN_SYSTEM_V2)
  profit: "#26A69A",
  green: "#26A69A",
  greenD: "#00897B",
  profitDark: "#00897B",
  profitBg: "rgba(38, 166, 154, 0.12)",

  loss: "#EF5350",
  red: "#EF5350",
  lossDark: "#E53935",
  lossBg: "rgba(239, 83, 80, 0.12)",

  // Text hierarchy
  t1: "#F0F2F5",
  t2: "#8B95A5",
  t3: "#5A6578",
  t4: "#3E4856",

  // Utility colors
  warning: "#FFB74D",
  gold: "#F59E0B",
  purple: "#A855F7",
  orange: "#F97316",

  // Shadows & Glows
  shadow: "0 2px 4px rgba(0,0,0,0.25)",
  shadowMd: "0 4px 12px rgba(0,0,0,0.35)",
  shadowLg: "0 8px 24px rgba(0,0,0,0.5)",

  glow: {
    profit: "0 0 20px rgba(38, 166, 154, 0.3), 0 0 40px rgba(38, 166, 154, 0.1)",
    loss: "0 0 20px rgba(239, 83, 80, 0.3), 0 0 40px rgba(239, 83, 80, 0.1)",
    accent: "0 0 20px rgba(0, 212, 255, 0.3), 0 0 40px rgba(0, 212, 255, 0.1)",
    warning: "0 0 20px rgba(255, 183, 77, 0.3)",
    gold: "0 0 20px rgba(245, 158, 11, 0.3)",
    purple: "0 0 20px rgba(168, 85, 247, 0.3)",
  },

  gradient: {
    profit: "linear-gradient(135deg, rgba(38,166,154,0.15) 0%, rgba(38,166,154,0.05) 100%)",
    loss: "linear-gradient(135deg, rgba(239,83,80,0.15) 0%, rgba(239,83,80,0.05) 100%)",
    accent: "linear-gradient(135deg, rgba(0,212,255,0.15) 0%, rgba(0,212,255,0.05) 100%)",
    card: "linear-gradient(180deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0) 100%)",
    shine: "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.1) 50%, transparent 100%)",
  },

  space: { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 24 },
  radius: { sm: 4, md: 6, lg: 8, xl: 12 }
};

// Ã¢â€â‚¬Ã¢â€â‚¬ Micro Components Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
export const Tag2 = ({ c = "accent", children, interactive = false }) => {
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

export const Btn = ({ children, v = "primary", sz = "md", Icon, onClick, disabled, cls = "", "aria-label": ariaLabel, ...rest }) => {
  const V = {
    primary: { background: C.accent, color: "#fff", fontWeight: 600, border: "none", boxShadow: C.shadow, hoverBg: C.accentHover, hoverTransform: "translateY(-1px)", activeTransform: "translateY(0) scale(0.98)" },
    outline: { background: "transparent", border: `1px solid ${C.border}`, color: C.accent, hoverBg: C.bg3, hoverBorder: C.borderLight, hoverTransform: "translateY(-1px)", activeTransform: "translateY(0) scale(0.98)" },
    ghost: { background: "transparent", border: "none", color: C.t2, hoverBg: C.bg3, hoverColor: C.t1, hoverTransform: "none", activeTransform: "scale(0.98)" },
    danger: { background: C.lossBg, border: `1px solid rgba(239,68,68,0.3)`, color: C.loss, hoverBg: "rgba(239,68,68,0.2)", hoverTransform: "translateY(-1px)", activeTransform: "translateY(0) scale(0.98)" },
    success: { background: C.profitBg, border: `1px solid rgba(34,197,94,0.3)`, color: C.profit, hoverBg: "rgba(34,197,94,0.2)", hoverTransform: "translateY(-1px)", activeTransform: "translateY(0) scale(0.98)" },
    gold: { background: C.gold, color: "#000", fontWeight: 600, border: "none", boxShadow: C.shadow, hoverBg: "#FCD34D", hoverTransform: "translateY(-1px)", activeTransform: "translateY(0) scale(0.98)" },
    red: { background: C.loss, color: "#fff", fontWeight: 600, border: "none", boxShadow: C.shadow, hoverBg: C.lossDark, hoverTransform: "translateY(-1px)", activeTransform: "translateY(0) scale(0.98)" },
  };
  const S = {
    xs: { padding: "4px 8px", fontSize: "9px" },
    sm: { padding: "5px 12px", fontSize: "10px" },
    md: { padding: "7px 16px", fontSize: "12px" },
    lg: { padding: "10px 24px", fontSize: "14px" }
  };
  const icon = Icon ? <Icon size={sz === "xs" ? 9 : sz === "sm" ? 11 : 13} aria-hidden="true" /> : null;
  const variant = V[v] || V.primary;
  const [isHovered, setIsHovered] = useState(false);
  const [isPressed, setIsPressed] = useState(false);
  const [isFocused, setIsFocused] = useState(false);

  const calculatedAriaLabel = ariaLabel || (typeof children === "string" ? children : undefined);

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      aria-label={calculatedAriaLabel}
      tabIndex={disabled ? -1 : 0}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => { setIsHovered(false); setIsPressed(false); }}
      onMouseDown={() => setIsPressed(true)}
      onMouseUp={() => setIsPressed(false)}
      onFocus={() => setIsFocused(true)}
      onBlur={() => setIsFocused(false)}
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
        outline: isFocused ? `2px solid ${C.accent}` : "none",
        outlineOffset: "2px",
        transition: "all 0.15s cubic-bezier(0.4, 0, 0.2, 1)"
      }}
      className={cls}
      {...rest}
    >
      {icon}{children}
    </button>
  );
};

export const Inp = ({ id, lbl, ph, type = "text", icon: Icon, hint, val, onChange, disabled = false, "aria-label": ariaLabel, ...rest }) => {
  const [isFocused, setIsFocused] = useState(false);
  const inputId = id || (lbl ? `inp-${lbl.toLowerCase().replace(/[^a-z0-9]/g, "-")}` : undefined);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: C.space.xs }}>
      {lbl && (
        <label htmlFor={inputId} style={{ color: C.t2, fontSize: 9, fontFamily: "monospace", fontWeight: 700, letterSpacing: 1.5, textTransform: "uppercase" }}>
          {lbl}
        </label>
      )}
      <div style={{ position: "relative" }}>
        {Icon && <Icon size={12} aria-hidden="true" style={{ color: isFocused ? C.accent : C.t3, position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", transition: "color 0.15s ease" }} />}
        <input
          id={inputId}
          type={type}
          value={val}
          onChange={onChange}
          placeholder={ph}
          disabled={disabled}
          aria-label={ariaLabel || lbl || ph}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          style={{
            background: C.bg3, border: `1px solid ${isFocused ? C.accent : C.border}`, color: C.t1,
            paddingLeft: Icon ? 32 : 12, paddingRight: 12, paddingTop: 8, paddingBottom: 8,
            opacity: disabled ? 0.45 : 1, cursor: disabled ? "not-allowed" : "text",
            borderRadius: C.radius.md, fontSize: 12, fontFamily: "monospace",
            width: "100%", outline: isFocused ? `2px solid ${C.accent}` : "none", outlineOffset: "1px",
            boxShadow: isFocused ? `0 0 0 3px ${C.accent}20` : "none",
            transition: "all 0.15s cubic-bezier(0.4, 0, 0.2, 1)"
          }}
          {...rest}
        />
      </div>
      {hint && <p style={{ color: C.t3, fontSize: 9, fontFamily: "monospace" }}>{hint}</p>}
    </div>
  );
};

export const Card = ({ children, cls = "", style = {}, hover = false, elevate = false }) => {
  const [isHovered, setIsHovered] = useState(false);
  return (
    <div
      onMouseEnter={() => hover && setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        background: C.bg2, border: `1px solid ${isHovered ? C.borderLight : C.border}`,
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

export const SectionH = ({ title, sub, right }) => (
  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: C.space.lg }}>
    <div>
      <h2 style={{ color: C.t1, fontSize: 18, fontWeight: 800, letterSpacing: -0.5 }}>{title}</h2>
      {sub && <p style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", marginTop: C.space.xs }}>{sub}</p>}
    </div>
    {right}
  </div>
);

export const PanelTitle = ({ title, sub, right }) => (
  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: C.space.md }}>
    <div>
      <h3 style={{ color: C.t1, fontSize: 13, fontWeight: 700, letterSpacing: -0.3 }}>{title}</h3>
      {sub && <p style={{ color: C.t2, fontSize: 10, fontFamily: "monospace", marginTop: C.space.xs }}>{sub}</p>}
    </div>
    {right}
  </div>
);

// Ã¢â€â‚¬Ã¢â€â‚¬ Table Components Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
export const Table = ({ children, cls = "" }) => (
  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, fontFamily: "monospace" }} className={cls}>
    {children}
  </table>
);

export const TableHead = ({ children }) => (
  <thead>
    <tr style={{ borderBottom: `1px solid ${C.border}` }}>{children}</tr>
  </thead>
);

export const TableHeader = ({ children, align = "left" }) => (
  <th style={{ color: C.t3, fontWeight: 700, padding: "10px 12px", textAlign: align, fontSize: 9, letterSpacing: 1, textTransform: "uppercase" }}>
    {children}
  </th>
);

export const TableRow = ({ children, highlight = false, interactive = false }) => {
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

export const TableCell = ({ children, align = "left", color = C.t1 }) => (
  <td style={{ color, padding: "10px 12px", textAlign: align, fontSize: 11 }}>{children}</td>
);

export const ProgressBar = ({ v, max, color = C.accent, h = 4 }) => (
  <div style={{ background: C.bg3, borderRadius: C.radius.sm, height: h, overflow: "hidden", border: `1px solid ${C.border}` }}>
    <div style={{ width: `${Math.min((v / max) * 100, 100)}%`, background: color, height: "100%", borderRadius: C.radius.sm, transition: "width 0.5s ease" }} />
  </div>
);

export const CustomTooltip = ({ active, payload, label, prefix = "$", suffix = "" }) => {
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

// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
//  UX FEEDBACK COMPONENTS
// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

export const Spinner = ({ size = 16, color = C.accent }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" style={{ animation: "spin 1s linear infinite" }}>
    <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    <circle cx="8" cy="8" r="6" fill="none" stroke={color} strokeWidth="2" strokeDasharray="10 20" strokeLinecap="round" />
  </svg>
);

export const LoadingOverlay = ({ text = "Loading..." }) => (
  <div style={{ position: "absolute", inset: 0, background: `${C.bg0}80`, backdropFilter: "blur(2px)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, zIndex: 50 }}>
    <Spinner size={24} />
    <span style={{ color: C.t2, fontSize: 11, fontFamily: "monospace" }}>{text}</span>
  </div>
);

export const LiveStatus = ({ isLive = true, label, pulse = true }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
    <span style={{ width: 8, height: 8, borderRadius: "50%", background: isLive ? C.profit : C.loss, boxShadow: isLive && pulse ? `0 0 0 4px ${C.profit}20` : undefined, animation: pulse && isLive ? "pulse 2s ease-in-out infinite" : undefined }}>
      <style>{`@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }`}</style>
    </span>
    <span style={{ color: isLive ? C.profit : C.loss, fontSize: 10, fontWeight: 600, fontFamily: "monospace", textTransform: "uppercase" }}>
      {label || (isLive ? "LIVE" : "OFFLINE")}
    </span>
  </div>
);

export const Toast = ({ id, type, message, onClose }) => {
  const icons = { trade: <ArrowUpRight size={14} color={C.profit} />, error: <XCircle size={14} color={C.loss} />, strategy: <Bot size={14} color={C.accent} />, info: <Info size={14} color={C.t2} /> };
  const colors = { trade: C.profit, error: C.loss, strategy: C.accent, info: C.t2 };
  useEffect(() => { const timer = setTimeout(() => onClose(id), 4000); return () => clearTimeout(timer); }, [id, onClose]);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", background: C.bg2, border: `1px solid ${C.border}`, borderLeft: `3px solid ${colors[type] || C.accent}`, borderRadius: 6, boxShadow: "0 4px 12px rgba(0,0,0,0.4)", minWidth: 240, maxWidth: 360, animation: "slideIn 0.2s ease-out" }}>
      <style>{`@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }`}</style>
      {icons[type] || icons.info}
      <span style={{ color: C.t1, fontSize: 12, fontFamily: "monospace", flex: 1 }}>{message}</span>
      <button onClick={() => onClose(id)} style={{ background: "transparent", border: "none", color: C.t3, cursor: "pointer", padding: 0 }}><XCircle size={14} /></button>
    </div>
  );
};

export const ToastContainer = ({ toasts, onRemove }) => (
  <div style={{ position: "fixed", top: 16, right: 16, display: "flex", flexDirection: "column", gap: 8, zIndex: 9999, pointerEvents: "none" }}>
    {toasts.map(t => (
      <div key={t.id} style={{ pointerEvents: "auto" }}>
        <Toast id={t.id} type={t.type} message={t.message} onClose={onRemove} />
      </div>
    ))}
  </div>
);

// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
//  ERROR HANDLING UTILITIES
// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

export const extractErrorMessage = (err, fallback = 'An error occurred') => {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'object' && detail !== null) {
    if (detail.reasons && Array.isArray(detail.reasons)) return detail.reasons.join(', ');
    if (detail.message) return detail.message;
    if (detail.error) return detail.error;
    try { return JSON.stringify(detail); } catch { /* fall through */ }
  }
  if (typeof detail === 'string') return detail;
  if (err?.response?.data?.message) return err.response.data.message;
  if (err?.response?.data?.error) return err.response.data.error;
  return err?.message || fallback;
};

export const getErrorType = (err) => {
  const status = err?.response?.status;
  if (status === 422) return 'validation';
  if (status === 404) return 'missing_data';
  if (status >= 500) return 'backend_failure';
  if (status === 401 || status === 403) return 'auth';
  if (status === 429) return 'rate_limit';
  return 'unknown';
};

// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
//  GLOBAL LOADING CONTEXT
// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

export const LoadingContext = React.createContext(null);

export const LoadingProvider = ({ children }) => {
  const [loadingStates, setLoadingStates] = useState({});
  const setLoading = useCallback((key, isLoading, message = "") => {
    setLoadingStates(prev => ({ ...prev, [key]: { loading: isLoading, message } }));
  }, []);
  const startLoading = useCallback((key, message = "Loading...") => setLoading(key, true, message), [setLoading]);
  const stopLoading = useCallback((key) => setLoading(key, false, ""), [setLoading]);
  const isLoading = useCallback((key) => loadingStates[key]?.loading || false, [loadingStates]);
  const getMessage = useCallback((key) => loadingStates[key]?.message || "", [loadingStates]);
  const anyLoading = useMemo(() => Object.values(loadingStates).some(s => s.loading), [loadingStates]);
  const value = useMemo(() => ({ setLoading, startLoading, stopLoading, isLoading, getMessage, anyLoading, states: loadingStates }), [setLoading, startLoading, stopLoading, isLoading, getMessage, anyLoading, loadingStates]);

  return (
    <LoadingContext.Provider value={value}>
      {children}
      {anyLoading && (
        <div style={{ position: "fixed", top: 0, left: 0, right: 0, bottom: 0, background: `${C.bg0}80`, backdropFilter: "blur(2px)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 16, zIndex: 9998 }}>
          <Spinner size={32} />
          <div style={{ color: C.t2, fontSize: 12, fontFamily: "monospace" }}>
            {Object.entries(loadingStates).filter(([_, s]) => s.loading).map(([k, s]) => s.message || k).join(" | ")}
          </div>
        </div>
      )}
    </LoadingContext.Provider>
  );
};

export const useLoading = (key) => {
  const context = React.useContext(LoadingContext);
  if (!context) throw new Error("useLoading must be used within LoadingProvider");
  if (key) {
    return { isLoading: context.isLoading(key), message: context.getMessage(key), start: (msg) => context.startLoading(key, msg), stop: () => context.stopLoading(key) };
  }
  return context;
};

export const setGlobalLoading = (key, loading, message) => {
  window.dispatchEvent(new CustomEvent("global-loading", { detail: { key, loading, message } }));
};

// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
//  SKELETON LOADERS
// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

export const SkeletonLine = ({ width = "100%", height = 12, mb = 0 }) => (
  <div style={{ width, height, background: `linear-gradient(90deg, ${C.bg3} 25%, ${C.bg4} 50%, ${C.bg3} 75%)`, backgroundSize: "200% 100%", animation: "shimmer 1.5s infinite", borderRadius: C.radius.sm, marginBottom: mb }}>
    <style>{`@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }`}</style>
  </div>
);

export const SkeletonCard = ({ height = 120 }) => (
  <div style={{ height, background: C.bg2, border: `1px solid ${C.border}`, borderRadius: C.radius.lg, padding: C.space.lg, display: "flex", flexDirection: "column", gap: C.space.md }}>
    <SkeletonLine width="60%" height={14} />
    <SkeletonLine width="80%" />
    <SkeletonLine width="40%" />
  </div>
);

export const SkeletonTable = ({ rows = 5 }) => (
  <div style={{ display: "flex", flexDirection: "column", gap: C.space.sm }}>
    <div style={{ display: "flex", gap: C.space.md, paddingBottom: C.space.sm, borderBottom: `1px solid ${C.border}` }}>
      <SkeletonLine width="20%" height={10} /><SkeletonLine width="30%" height={10} />
      <SkeletonLine width="25%" height={10} /><SkeletonLine width="25%" height={10} />
    </div>
    {Array.from({ length: rows }).map((_, i) => (
      <div key={i} style={{ display: "flex", gap: C.space.md, padding: `${C.space.sm}px 0` }}>
        <SkeletonLine width="20%" /><SkeletonLine width="30%" />
        <SkeletonLine width="25%" /><SkeletonLine width="25%" />
      </div>
    ))}
  </div>
);

// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
//  ERROR STATE COMPONENT
// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

export const ErrorState = ({ title = "Failed to load data", message = "Something went wrong", onRetry, compact = false }) => (
  <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: compact ? C.space.lg : C.space.xxl, gap: C.space.md, textAlign: "center" }}>
    <AlertTriangle size={compact ? 24 : 32} color={C.warning} />
    <div>
      <p style={{ color: C.t1, fontSize: compact ? 12 : 14, fontWeight: 600, marginBottom: 4 }}>{title}</p>
      {message && <p style={{ color: C.t3, fontSize: compact ? 10 : 11 }}>{message}</p>}
    </div>
    {onRetry && (
      <button onClick={onRetry} style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 12px", background: C.bg3, border: `1px solid ${C.border}`, borderRadius: C.radius.md, color: C.t2, fontSize: 11, fontFamily: "monospace", cursor: "pointer", transition: "all 0.15s ease" }}
        onMouseEnter={(e) => { e.target.style.borderColor = C.borderLight; e.target.style.color = C.t1; }}
        onMouseLeave={(e) => { e.target.style.borderColor = C.border; e.target.style.color = C.t2; }}
      >
        <RefreshCw size={12} />Retry
      </button>
    )}
  </div>
);

// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
//  REAL-TIME DATA POLLING HOOK
// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

export const usePolling = (fetchFn, interval = 5000, options = {}) => {
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

// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
//  LIVE LOG STREAM
// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

export const LiveLogStream = React.memo(function LiveLogStream({ logs = [], maxHeight = 200 }) {
  const scrollRef = useRef(null);
  const [autoScroll, setAutoScroll] = useState(true);
  useEffect(() => { if (autoScroll && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }, [logs, autoScroll]);

  const icons = { info: <Info size={10} color={C.t3} />, trade: <ArrowUpRight size={10} color={C.profit} />, signal: <Zap size={10} color={C.warning} />, error: <XCircle size={10} color={C.loss} />, system: <Activity size={10} color={C.accent} /> };

  return (
    <div style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: C.radius.lg, overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 12px", borderBottom: `1px solid ${C.border}`, background: C.bg3 }}>
        <span style={{ color: C.t2, fontSize: 10, fontWeight: 600, textTransform: "uppercase" }}>Live Logs</span>
        <button onClick={() => setAutoScroll(!autoScroll)} style={{ fontSize: 9, color: autoScroll ? C.accent : C.t3, background: "transparent", border: "none", cursor: "pointer" }}>
          {autoScroll ? "Auto-scroll ON" : "Auto-scroll OFF"}
        </button>
      </div>
      <div ref={scrollRef} style={{ maxHeight, overflowY: "auto", padding: C.space.md, fontFamily: "monospace", fontSize: 10 }}>
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
                <span style={{ color: log.type === "error" ? C.loss : log.type === "trade" ? C.profit : C.t2 }}>{log.message}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
});

// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
//  ENHANCED LIVE STATUS (with loading state)
// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

export const LiveStatusV2 = React.memo( ({ status = "stopped", label }) => {
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
      <span style={{ width: 8, height: 8, borderRadius: "50%", background: config.color, boxShadow: config.bgPulse ? `0 0 0 4px ${config.color}20` : undefined, animation: config.bgPulse ? "pulse 2s ease-in-out infinite" : config.blink ? "blink 1s ease-in-out infinite" : undefined }}>
        <style>{`@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } } @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }`}</style>
      </span>
      <span style={{ color: config.color, fontSize: 10, fontWeight: 600, fontFamily: "monospace", textTransform: "uppercase" }}>{config.label}</span>
    </div>
  );
});

// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
//  BUTTON WITH LOADING STATE
// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

export const AsyncBtn = React.memo( ({ children, onClick, loading = false, loadingText, disabled, v = "primary", sz = "md", Icon }) => {
  const isDisabled = disabled || loading;
  return (
    <Btn v={v} sz={sz} Icon={loading ? null : Icon} onClick={onClick} disabled={isDisabled}>
      {loading ? (<><Spinner size={sz === "lg" ? 14 : sz === "sm" ? 10 : 12} /><span>{loadingText || children}</span></>) : children}
    </Btn>
  );
});

// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
//  PREMIUM UI COMPONENTS
// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

export const EmptyState = ({ icon: Icon, title, subtitle, action, actionLabel, hint, size = "md" }) => {
  const sizes = { sm: { icon: 24, title: 12, subtitle: 10, padding: 20 }, md: { icon: 40, title: 14, subtitle: 12, padding: 40 }, lg: { icon: 64, title: 18, subtitle: 14, padding: 60 } };
  const s = sizes[size];
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: s.padding, gap: 12, textAlign: "center" }}>
      {Icon && (<div style={{ animation: "float 3s ease-in-out infinite", opacity: 0.5 }}>
        <style>{`@keyframes float { 0%, 100% { transform: translateY(0px); } 50% { transform: translateY(-8px); } }`}</style>
        <Icon size={s.icon} color={C.t3} strokeWidth={1} />
      </div>)}
      <div>
        <p style={{ color: C.t2, fontSize: s.title, fontWeight: 600, marginBottom: 4 }}>{title}</p>
        {subtitle && <p style={{ color: C.t3, fontSize: s.subtitle }}>{subtitle}</p>}
      </div>
      {hint && <div style={{ background: C.bg3, border: `1px dashed ${C.border}`, borderRadius: C.radius.md, padding: "8px 12px", marginTop: 8 }}><p style={{ color: C.t4, fontSize: 10, fontStyle: "italic" }}>Ã°Å¸â€™Â¡ {hint}</p></div>}
      {action && actionLabel && <Btn v="outline" sz="sm" onClick={action}>{actionLabel}</Btn>}
    </div>
  );
};

export const PnLBadge = React.memo( ({ value, prefix = "$", size = "md", animated = true }) => {
  const isProfit = value >= 0;
  const color = isProfit ? C.profit : C.loss;
  const glow = isProfit ? C.glow.profit : C.glow.loss;
  const sizes = { sm: 12, md: 16, lg: 24, xl: 32 };
  return (
    <span style={{ color, fontSize: sizes[size], fontWeight: 700, fontFamily: "monospace", textShadow: glow, animation: animated && value !== 0 ? "pulseGlow 2s ease-in-out infinite" : undefined, display: "inline-flex", alignItems: "center", gap: 4 }}>
      <style>{`@keyframes pulseGlow { 0%, 100% { text-shadow: ${glow}; } 50% { text-shadow: none; } }`}</style>
      {isProfit ? "+" : ""}{prefix}{Math.abs(value).toLocaleString()}
    </span>
  );
});

export const AnimatedNumber = React.memo( ({ value, prefix = "", suffix = "", duration = 500 }) => {
  const [display, setDisplay] = useState(value);
  const prevValue = useRef(value);
  useEffect(() => {
    let animId;
    const start = prevValue.current, end = value, diff = end - start, startTime = performance.now();
    const animate = (t) => {
      const progress = Math.min((t - startTime) / duration, 1);
      const easeOut = 1 - Math.pow(1 - progress, 3);
      setDisplay(start + diff * easeOut);
      if (progress < 1) {
        animId = requestAnimationFrame(animate);
      } else {
        prevValue.current = value;
      }
    };
    animId = requestAnimationFrame(animate);
    return () => {
      if (animId) cancelAnimationFrame(animId);
    };
  }, [value, duration]);
  return <span>{prefix}{display.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}{suffix}</span>;
});

export const RiskMeter = React.memo( ({ value, max, label, warningAt = 0.7, dangerAt = 0.9 }) => {
  const percentage = Math.min((value / max) * 100, 100);
  const ratio = value / max;
  let color = C.profit, status = "SAFE";
  if (ratio >= dangerAt) { color = C.loss; status = "CRITICAL"; }
  else if (ratio >= warningAt) { color = C.warning; status = "WARNING"; }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ color: C.t2, fontSize: 10, fontWeight: 600 }}>{label}</span>
        <span style={{ color, fontSize: 10, fontWeight: 700 }}>{status}</span>
      </div>
      <div style={{ height: 6, background: C.bg3, borderRadius: 3, overflow: "hidden", position: "relative" }}>
        <div style={{ position: "absolute", left: `${warningAt * 100}%`, top: 0, bottom: 0, width: 1, background: C.warning, opacity: 0.5 }} />
        <div style={{ position: "absolute", left: `${dangerAt * 100}%`, top: 0, bottom: 0, width: 1, background: C.loss, opacity: 0.5 }} />
        <div style={{ width: `${percentage}%`, height: "100%", background: color, borderRadius: 3, transition: "width 0.5s ease, background 0.3s ease", boxShadow: ratio >= warningAt ? C.glow[color === C.loss ? "loss" : color === C.warning ? "warning" : "profit"] : undefined }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: C.t3 }}>
        <span>{value.toLocaleString()}</span><span>{max.toLocaleString()}</span>
      </div>
    </div>
  );
});

export const PremiumCard = ({ children, title, icon: Icon, action, glowOnHover = false, glowColor = "accent", borderAccent = false, style = {} }) => {
  const [isHovered, setIsHovered] = useState(false);
  return (
    <div onMouseEnter={() => setIsHovered(true)} onMouseLeave={() => setIsHovered(false)} style={{ background: C.bg2, border: `1px solid ${borderAccent && isHovered ? C[glowColor] || C.accent : C.border}`, borderRadius: C.radius.lg, overflow: "hidden", transition: "all 0.25s cubic-bezier(0.4, 0, 0.2, 1)", transform: isHovered ? "translateY(-2px)" : "translateY(0)", boxShadow: isHovered && glowOnHover ? `${C.shadowMd}, ${C.glow[glowColor] || C.glow.accent}` : C.shadow, ...style }}>
      {title && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderBottom: `1px solid ${C.border}`, background: C.gradient.card }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>{Icon && <Icon size={14} color={C.t2} />}<span style={{ color: C.t1, fontSize: 12, fontWeight: 600 }}>{title}</span></div>
          {action}
        </div>
      )}
      <div style={{ padding: title ? "16px" : 0 }}>{children}</div>
    </div>
  );
};

export const StatusDot = React.memo( ({ status = "idle", size = 8, pulse = true }) => {
  const colors = { idle: C.t3, live: C.profit, running: C.profit, stopped: C.t3, backtesting: C.accent, paused: C.warning, warning: C.warning, error: C.loss, active: C.accent };
  const color = colors[status] || colors.idle;
  const shouldPulse = pulse && (status === "live" || status === "active" || status === "running");
  return (
    <span style={{ width: size, height: size, borderRadius: "50%", background: color, display: "inline-block", boxShadow: shouldPulse ? `0 0 0 ${size / 2}px ${color}40` : undefined, animation: shouldPulse ? "statusPulse 2s ease-in-out infinite" : undefined }}>
      <style>{`@keyframes statusPulse { 0%, 100% { box-shadow: 0 0 0 0px ${color}60; } 50% { box-shadow: 0 0 0 ${size}px ${color}00; } }`}</style>
    </span>
  );
});

export const MiniSparkline = React.memo( ({ data = [], width = 80, height = 24, color = C.accent, fill = true }) => {
  if (!data.length) return <div style={{ width, height }} />;
  const min = Math.min(...data), max = Math.max(...data), range = max - min || 1;
  const points = data.map((v, i) => `${(i / (data.length - 1)) * width},${height - ((v - min) / range) * height}`).join(" ");
  return (
    <svg width={width} height={height} style={{ overflow: "visible" }}>
      <defs>
        <linearGradient id={`sparkFill-${color.replace("#", "")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.3} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      {fill && <polygon points={`0,${height} ${points} ${width},${height}`} fill={`url(#sparkFill-${color.replace("#", "")})`} />}
      <polyline points={points} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
});



