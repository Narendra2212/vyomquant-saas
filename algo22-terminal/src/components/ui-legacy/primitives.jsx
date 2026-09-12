/**
 * ui-legacy/primitives.jsx
 *
 * Design system tokens, primitive UI components, and shared hooks
 * extracted from App.jsx (was ~lines 208-1488).
 *
 * DO NOT add page-level logic here. This file should only contain:
 *   - The C compatibility shim (DERIVED from src/design/tokens.js — not a token
 *     source; see the comment on the object itself)
 *   - Stateless and lightly-stateful micro-components
 *   - Shared hooks (usePolling)
 *   - Error/loading utilities
 */

import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  ArrowUpRight, XCircle, Bot, Info, Activity,
  AlertTriangle, RefreshCw, Zap
} from "lucide-react";
import { Button } from "../ui/Button";
import { token } from "../../design/tokens";

// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
//  DESIGN SYSTEM - PROFESSIONAL TRADING THEME
// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
/**
 * The px root font size the token rem values are expressed against.
 *
 * Not a design token: `tokens.css` declares no `font-size` on `:root`, so this
 * is the browser default, and it exists here only to project a rem token onto
 * the unitless numbers the legacy inline styles below still pass to React.
 */
const REM_PX = 16;

/**
 * Project a token length (`'0.75rem'`, `'6px'`) onto the unitless px number the
 * legacy `C.space` / `C.radius` scales have always exposed.
 *
 * These two scales stay numeric on purpose. `C.space.*` is consumed
 * arithmetically (`PaperTrading.jsx` computes `clientWidth - 2 * C.space.xl`)
 * and interpolated (`` `${C.space.sm}px 0` ``), so handing back a rem string
 * would produce `NaN` and `'0.5rempx'` respectively. The *values* are still
 * derived from `tokens.css`; only the unit is legacy.
 */
const legacyPx = (length) =>
  length.endsWith("rem") ? Math.round(parseFloat(length) * REM_PX) : parseInt(length, 10);

/**
 * COMPATIBILITY SHIM — Requirement 1.1.
 *
 * `C` is no longer a token source. Every value below is DERIVED from
 * `src/design/tokens.js`, which is generated from `src/styles/tokens.css`.
 * There is not one colour, shadow, radius or spacing literal in this object,
 * so `C.profit` and `var(--color-status-profit)` cannot disagree.
 *
 * This object is FROZEN and CLOSED: adding a key is a lint error (see the
 * `vyom/no-new-legacy-token` rule in `eslint.config.js`). It shrinks as pages
 * migrate to `components/ds/*` and is deleted at the end of step M9.
 *
 * DO NOT import `C` into any new component. Import `token` or use a Tailwind
 * utility.
 *
 * Deliberate collapses, all from design.md §3.4 — each of these was two values
 * that carried no semantic distinction, and is now one:
 *   - `bg3`/`bg4` → `surface.inset`; `borderLight`/`borderHover` → `line.strong`
 *   - `cyanD`/`accentMuted`/`blue` → `brand.base` (`#2962FF` is retired)
 *   - `greenD`/`profitDark` → `status.profit.fg`; `lossDark` → `status.loss.fg`
 *   - `t3`/`t4` → `content.muted` (NON-TEXT ONLY at 3.2:1)
 *   - `warning`/`gold`/`orange` → `status.warning.fg`: one amber, `#FFB74D` retired
 *   - `purple` → `status.neutral.fg`: the decorative hue is retired
 *
 * `glow.*` and `gradient.*` are all `'none'`. That is Requirement 1.5 satisfied
 * app-wide from this one edit, with no page touched: every decorative glow and
 * shine gradient stops rendering at each of its ~700 call sites at once.
 */
export const C = Object.freeze({
  // Background hierarchy → surface
  bg: token.surface.canvas,
  bg0: token.surface.canvas,
  bg1: token.surface.panel,
  bg2: token.surface.raised,
  bg3: token.surface.inset,
  bg4: token.surface.inset,

  // Border system → line
  border: token.line.default,
  borderLight: token.line.strong,
  borderHover: token.line.strong,

  // Accent colours → brand
  cyan: token.brand.base,
  cyanL: token.brand.hover,
  cyanD: token.brand.base,
  cyanDim: token.brand.wash,
  accent: token.brand.base,
  accentHover: token.brand.hover,
  accentMuted: token.brand.base,
  blue: token.brand.base,

  // Profit / Loss → semantic status
  profit: token.status.profit.fg,
  green: token.status.profit.fg,
  greenD: token.status.profit.fg,
  profitDark: token.status.profit.fg,
  profitBg: token.status.profit.wash,

  loss: token.status.loss.fg,
  red: token.status.loss.fg,
  lossDark: token.status.loss.fg,
  lossBg: token.status.loss.wash,

  // Text hierarchy → content
  t1: token.content.primary,
  t2: token.content.secondary,
  t3: token.content.muted,
  t4: token.content.muted,

  // Utility colours → semantic status
  warning: token.status.warning.fg,
  gold: token.status.warning.fg,
  purple: token.status.neutral.fg,
  orange: token.status.warning.fg,

  // Elevation → shadow. No coloured glows.
  shadow: token.shadow.panel,
  shadowMd: token.shadow.raised,
  shadowLg: token.shadow.overlay,

  // Requirement 1.5: retired, kept only so existing call sites resolve.
  glow: Object.freeze({
    profit: "none",
    loss: "none",
    accent: "none",
    warning: "none",
    gold: "none",
    purple: "none",
  }),

  gradient: Object.freeze({
    profit: "none",
    loss: "none",
    accent: "none",
    card: "none",
    shine: "none",
  }),

  space: Object.freeze({
    xs: legacyPx(token.space["1"]),
    sm: legacyPx(token.space["2"]),
    md: legacyPx(token.space["3"]),
    lg: legacyPx(token.space["4"]),
    xl: legacyPx(token.space["5"]),
    xxl: legacyPx(token.space["6"]),
  }),

  radius: Object.freeze({
    sm: legacyPx(token.radius.sm),
    md: legacyPx(token.radius.md),
    lg: legacyPx(token.radius.lg),
    xl: legacyPx(token.radius.xl),
  }),
});

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
//  GLOBAL LOADING CONTEXT
// Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â

export const LoadingContext = React.createContext(null);

/**
 * Keyed loading registry — vyomquant-ui-redesign task 10.9. design.md §5.1.
 * Requirement 14.2.
 *
 * The provider and its whole context API (`setLoading`, `startLoading`,
 * `stopLoading`, `isLoading`, `getMessage`, `anyLoading`, `states`) are
 * unchanged. What is gone is the full-screen blocking overlay this used to
 * render whenever `anyLoading` was true: it dimmed and blocked the entire
 * application — sidebar, top bar and all nine other panels — for a load that
 * may have belonged to one card, which is the opposite of the per-element
 * loading Requirement 14.2 asks for.
 *
 * The replacement is `components/ds/LoadingState.jsx`, whose `kind` occupies the
 * region of the element that is actually loading and leaves the shell live and
 * interactive. Consumers that want a page-wide indicator ask for
 * `kind="page"` at the page's own root rather than at the provider's.
 *
 * `anyLoading` stays on the context: it is part of the published API and the
 * overlay was only one possible reading of it.
 */
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

export const AsyncBtn = React.memo( ({ children, onClick, loading = false, loadingText, disabled, variant = "primary", size = "md", icon: Icon }) => {
  const isDisabled = disabled || loading;
  return (
    <Button variant={variant} size={size} icon={loading ? null : Icon} onClick={onClick} disabled={isDisabled}>
      {loading ? (<><Spinner size={size === "lg" ? 14 : size === "sm" ? 10 : 12} /><span>{loadingText || children}</span></>) : children}
    </Button>
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
      {hint && <div style={{ background: C.bg3, border: `1px dashed ${C.border}`, borderRadius: C.radius.md, padding: "8px 12px", marginTop: 8 }}><p style={{ color: C.t4, fontSize: 10, fontStyle: "italic" }}>💡 {hint}</p></div>}
      {action && actionLabel && <Button variant="outline" size="sm" onClick={action}>{actionLabel}</Button>}
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



