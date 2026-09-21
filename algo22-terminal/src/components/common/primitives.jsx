/**
 * common/primitives.jsx — vyomquant-ui-redesign task 27.2, FINAL STAGE A.
 * Requirements 1.1, 1.3. design.md §3.4, §14.4.
 *
 * ===========================================================================
 * WHAT THIS MODULE IS
 * ===========================================================================
 * The eleven components that `components/ui-legacy/primitives.jsx` still has a
 * caller for, rehomed. Nothing else came with them: the shim exported 36 names
 * and 25 of those had no importer anywhere in `src/` — they were reachable only
 * because `components/ui/index.js` re-exported the whole surface with
 * `export * from '../ui-legacy/primitives'`, and nothing imports that barrel.
 * That line is deleted in the same change, which is what makes the remaining 25
 * unreachable and lets stage B delete the shim outright.
 *
 * Each body is the one the shim shipped, character for character, apart from its
 * colour reads. Same props, same defaults, same markup, same class names, same
 * `<style>` children, same `React.memo` wrappers. These components are NOT
 * changed by this task — only their home and their token source move.
 *
 * ===========================================================================
 * WHY THIS IS NOT `ui-legacy` ANY MORE
 * ===========================================================================
 * `ui-legacy` named one thing: a module that read the `C` compatibility shim.
 * `C` was never a token source — every value in it was already derived from
 * `design/tokens.js` — but it was an extra hop, it was frozen and closed, and it
 * existed only so that ~2050 call sites could keep resolving while the pages
 * migrated one at a time (design.md §3.4). Every one of those call sites is now
 * gone except the shim's own, so the hop buys nothing and the name is a lie: a
 * component that reads `token.*` directly is not legacy, it is just not a `ds/`
 * primitive yet.
 *
 * So the eleven move to `common/`, which says what is actually true of them —
 * shared, tokened, and still to be consolidated. They read `token` from
 * `design/tokens.js` with no indirection, and this module imports nothing from
 * `ui-legacy`.
 *
 * ===========================================================================
 * INTENDED `ds/` SUCCESSORS
 * ===========================================================================
 * Six of the eleven duplicate something `components/ds/*` already does properly:
 *
 *   Inp         -> ds/Field           (labelled control; never names itself from
 *                                      the placeholder, which `Inp` does)
 *   SectionH    -> ds/PageHeader
 *   PanelTitle  -> ds/SectionHeader
 *   Spinner     -> ds/LoadingState    (`kind` occupies the loading region)
 *   Tag2        -> ds/StatusBadge     (hue from design/semantic.js, no colour prop)
 *   StatusDot   -> ds/StatusBadge     (same; a dot is colour-only state today)
 *   RiskMeter   -> ds/RiskIndicator
 *
 * The other four — `ProgressBar`, `Toast`, `ToastContainer`, `LoadingProvider` —
 * have no `ds/` counterpart named in the spec and stay as they are.
 *
 * ADOPTING ANY OF THOSE SUCCESSORS IS A PER-CALL-SITE RESTRUCTURE AND IS NOT PART
 * OF THIS TASK. The props differ, the markup differs, and the hue decisions
 * differ — `ds/StatusBadge` derives its colour from a state word where `Tag2`
 * takes one of nine colour names, and `ds/Field` renders an accessible name from
 * its label where `Inp` falls back to the placeholder. Swapping them changes what
 * each of the fifteen consuming pages renders, so each one is its own change with
 * its own review. The table is here so the remaining consolidation is
 * discoverable, not so it can be done in passing.
 *
 * ===========================================================================
 * WHAT CHANGED IN THE BODIES, AND WHAT DID NOT
 * ===========================================================================
 * Every `C.` read became the token it already resolved to, so nothing renders
 * differently: `C.t1` -> `token.content.primary`, `C.border` ->
 * `token.line.default`, `C.accent` -> `token.brand.base`, and so on through
 * design.md §3.4's collapse table. Three cases are worth naming:
 *
 * DEFAULT PROP VALUES ARE REAL READS. `ProgressBar`'s `color = C.accent` and
 * `Spinner`'s `color = C.accent` are now `token.brand.base`. They are the
 * rendered default for every call site that passes no `color`.
 *
 * SPACING IS A REM STRING WHERE IT WAS A PX NUMBER. The shim projected
 * `C.space.*` through `legacyPx` to a unitless number; `token.space['n']` is a
 * rem string. Every read in this module is a plain style value — `gap`,
 * `marginTop`, `marginBottom` — so React writes `0.25rem` where it wrote `4px`,
 * which is the same length at the browser default root size. NOT ONE of them is
 * read arithmetically or interpolated, so no `parseFloat`/`Math.round` projection
 * is needed here (`pages/PaperTrading.jsx` keeps one for exactly that case). The
 * four `C.radius.*` reads are the same story and the token values are already px
 * strings, so those are byte-identical.
 *
 * THE DEAD GLOW TERM IS DROPPED RATHER THAN CARRIED. `RiskMeter`'s
 * `boxShadow: ratio >= warningAt ? C.glow[…] : undefined` selected between three
 * `C.glow` entries that Requirement 1.5 retired to the literal `'none'` in M1, so
 * the property has rendered nothing either way since then. Writing
 * `boxShadow: … ? 'none' : undefined` would preserve a decision that no longer
 * has two outcomes; the property is gone instead. Nothing else in the eleven read
 * `C.glow` or `C.gradient`.
 *
 * The seventeen `rgba()` washes in `Tag2` and the one in `Toast`'s shadow came
 * across untouched. They are off-palette — the retired #00C853 green, #FF3D00,
 * #FFAB00, #7C4DFF and #FFD600 — and replacing them is a colour decision, not a
 * rehome. They move with the components they belong to, which is why this file
 * takes over the shim's `no-colour-literals` budget entry.
 *
 * DO NOT add a component here. New shared UI belongs in `components/ds/*`.
 */

import React, { useState, useEffect, useCallback, useMemo } from "react";
import { ArrowUpRight, XCircle, Bot, Info } from "lucide-react";
import { token } from "../../design/tokens";

// ── Micro components ──────────────────────────────────────────────────────────

export const Tag2 = ({ c = "accent", children, interactive = false }) => {
  const [isHovered, setIsHovered] = useState(false);
  const m = {
    accent: { bg: token.surface.inset, border: token.line.strong, text: token.brand.base, hoverBg: token.surface.inset },
    profit: { bg: token.status.profit.wash, border: "rgba(0,200,83,0.3)", text: token.status.profit.fg, hoverBg: "rgba(0,200,83,0.15)" },
    loss: { bg: token.status.loss.wash, border: "rgba(255,61,0,0.3)", text: token.status.loss.fg, hoverBg: "rgba(255,61,0,0.15)" },
    warning: { bg: "rgba(255,171,0,0.1)", border: "rgba(255,171,0,0.3)", text: token.status.warning.fg, hoverBg: "rgba(255,171,0,0.15)" },
    purple: { bg: "rgba(124,77,255,0.1)", border: "rgba(124,77,255,0.3)", text: token.status.neutral.fg, hoverBg: "rgba(124,77,255,0.15)" },
    gold: { bg: "rgba(255,214,0,0.1)", border: "rgba(255,214,0,0.3)", text: token.status.warning.fg, hoverBg: "rgba(255,214,0,0.15)" },
    cyan: { bg: token.surface.inset, border: token.line.strong, text: token.brand.base, hoverBg: token.surface.inset },
    green: { bg: token.status.profit.wash, border: "rgba(0,200,83,0.3)", text: token.status.profit.fg, hoverBg: "rgba(0,200,83,0.15)" },
    red: { bg: token.status.loss.wash, border: "rgba(255,61,0,0.3)", text: token.status.loss.fg, hoverBg: "rgba(255,61,0,0.15)" },
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
        boxShadow: isHovered && interactive ? token.shadow.raised : token.shadow.panel,
        borderRadius: token.radius.sm,
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
    <div style={{ display: "flex", flexDirection: "column", gap: token.space["1"] }}>
      {lbl && (
        <label htmlFor={inputId} style={{ color: token.content.secondary, fontSize: 9, fontFamily: "monospace", fontWeight: 700, letterSpacing: 1.5, textTransform: "uppercase" }}>
          {lbl}
        </label>
      )}
      <div style={{ position: "relative" }}>
        {Icon && <Icon size={12} aria-hidden="true" style={{ color: isFocused ? token.brand.base : token.content.muted, position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", transition: "color 0.15s ease" }} />}
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
            background: token.surface.inset, border: `1px solid ${isFocused ? token.brand.base : token.line.default}`, color: token.content.primary,
            paddingLeft: Icon ? 32 : 12, paddingRight: 12, paddingTop: 8, paddingBottom: 8,
            opacity: disabled ? 0.45 : 1, cursor: disabled ? "not-allowed" : "text",
            borderRadius: token.radius.md, fontSize: 12, fontFamily: "monospace",
            width: "100%", outline: isFocused ? `2px solid ${token.brand.base}` : "none", outlineOffset: "1px",
            boxShadow: isFocused ? `0 0 0 3px ${token.brand.base}20` : "none",
            transition: "all 0.15s cubic-bezier(0.4, 0, 0.2, 1)"
          }}
          {...rest}
        />
      </div>
      {hint && <p style={{ color: token.content.muted, fontSize: 9, fontFamily: "monospace" }}>{hint}</p>}
    </div>
  );
};

export const SectionH = ({ title, sub, right }) => (
  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: token.space["4"] }}>
    <div>
      <h2 style={{ color: token.content.primary, fontSize: 18, fontWeight: 800, letterSpacing: -0.5 }}>{title}</h2>
      {sub && <p style={{ color: token.content.secondary, fontSize: 10, fontFamily: "monospace", marginTop: token.space["1"] }}>{sub}</p>}
    </div>
    {right}
  </div>
);

export const PanelTitle = ({ title, sub, right }) => (
  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: token.space["3"] }}>
    <div>
      <h3 style={{ color: token.content.primary, fontSize: 13, fontWeight: 700, letterSpacing: -0.3 }}>{title}</h3>
      {sub && <p style={{ color: token.content.secondary, fontSize: 10, fontFamily: "monospace", marginTop: token.space["1"] }}>{sub}</p>}
    </div>
    {right}
  </div>
);

export const ProgressBar = ({ v, max, color = token.brand.base, h = 4 }) => (
  <div style={{ background: token.surface.inset, borderRadius: token.radius.sm, height: h, overflow: "hidden", border: `1px solid ${token.line.default}` }}>
    <div style={{ width: `${Math.min((v / max) * 100, 100)}%`, background: color, height: "100%", borderRadius: token.radius.sm, transition: "width 0.5s ease" }} />
  </div>
);

// ── UX feedback ───────────────────────────────────────────────────────────────

export const Spinner = ({ size = 16, color = token.brand.base }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" style={{ animation: "spin 1s linear infinite" }}>
    <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    <circle cx="8" cy="8" r="6" fill="none" stroke={color} strokeWidth="2" strokeDasharray="10 20" strokeLinecap="round" />
  </svg>
);

export const Toast = ({ id, type, message, onClose }) => {
  const icons = { trade: <ArrowUpRight size={14} color={token.status.profit.fg} />, error: <XCircle size={14} color={token.status.loss.fg} />, strategy: <Bot size={14} color={token.brand.base} />, info: <Info size={14} color={token.content.secondary} /> };
  const colors = { trade: token.status.profit.fg, error: token.status.loss.fg, strategy: token.brand.base, info: token.content.secondary };
  useEffect(() => { const timer = setTimeout(() => onClose(id), 4000); return () => clearTimeout(timer); }, [id, onClose]);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", background: token.surface.raised, border: `1px solid ${token.line.default}`, borderLeft: `3px solid ${colors[type] || token.brand.base}`, borderRadius: 6, boxShadow: "0 4px 12px rgba(0,0,0,0.4)", minWidth: 240, maxWidth: 360, animation: "slideIn 0.2s ease-out" }}>
      <style>{`@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }`}</style>
      {icons[type] || icons.info}
      <span style={{ color: token.content.primary, fontSize: 12, fontFamily: "monospace", flex: 1 }}>{message}</span>
      <button onClick={() => onClose(id)} style={{ background: "transparent", border: "none", color: token.content.muted, cursor: "pointer", padding: 0 }}><XCircle size={14} /></button>
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

// ── Global loading context ────────────────────────────────────────────────────

/**
 * Private to `LoadingProvider`, deliberately not exported.
 *
 * The shim exported this context alongside `useLoading` and `setGlobalLoading`,
 * and nothing in `src/` imported any of the three — they were reachable only
 * through the `components/ui/index.js` barrel, which nothing imports either. So
 * the provider comes across (`App.jsx` mounts it) and its consumer API does not.
 * A context with no exported reader is the provider's own plumbing, and keeping
 * it private means the only way to add a consumer is to export it deliberately.
 */
const LoadingContext = React.createContext(null);

/**
 * Keyed loading registry — vyomquant-ui-redesign task 10.9. design.md §5.1.
 * Requirement 14.2.
 *
 * The provider and its whole context API (`setLoading`, `startLoading`,
 * `stopLoading`, `isLoading`, `getMessage`, `anyLoading`, `states`) are
 * unchanged. What is gone — since task 10.9, not since this rehome — is the
 * full-screen blocking overlay this used to render whenever `anyLoading` was
 * true: it dimmed and blocked the entire application, sidebar, top bar and all
 * nine other panels, for a load that may have belonged to one card, which is the
 * opposite of the per-element loading Requirement 14.2 asks for.
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

// ── Status presentation ───────────────────────────────────────────────────────

export const RiskMeter = React.memo( ({ value, max, label, warningAt = 0.7, dangerAt = 0.9 }) => {
  const percentage = Math.min((value / max) * 100, 100);
  const ratio = value / max;
  let color = token.status.profit.fg, status = "SAFE";
  if (ratio >= dangerAt) { color = token.status.loss.fg; status = "CRITICAL"; }
  else if (ratio >= warningAt) { color = token.status.warning.fg; status = "WARNING"; }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ color: token.content.secondary, fontSize: 10, fontWeight: 600 }}>{label}</span>
        <span style={{ color, fontSize: 10, fontWeight: 700 }}>{status}</span>
      </div>
      <div style={{ height: 6, background: token.surface.inset, borderRadius: 3, overflow: "hidden", position: "relative" }}>
        <div style={{ position: "absolute", left: `${warningAt * 100}%`, top: 0, bottom: 0, width: 1, background: token.status.warning.fg, opacity: 0.5 }} />
        <div style={{ position: "absolute", left: `${dangerAt * 100}%`, top: 0, bottom: 0, width: 1, background: token.status.loss.fg, opacity: 0.5 }} />
        {/* The `boxShadow` that was here selected between three `C.glow` entries, all of
            which Requirement 1.5 retired to the literal `'none'` in M1 — so the bar has
            rendered no glow at or above `warningAt` since then. Dropped rather than
            carried across as `'none'`: see the docblock. */}
        <div style={{ width: `${percentage}%`, height: "100%", background: color, borderRadius: 3, transition: "width 0.5s ease, background 0.3s ease" }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: token.content.muted }}>
        <span>{value.toLocaleString()}</span><span>{max.toLocaleString()}</span>
      </div>
    </div>
  );
});

export const StatusDot = React.memo( ({ status = "idle", size = 8, pulse = true }) => {
  const colors = { idle: token.content.muted, live: token.status.profit.fg, running: token.status.profit.fg, stopped: token.content.muted, backtesting: token.brand.base, paused: token.status.warning.fg, warning: token.status.warning.fg, error: token.status.loss.fg, active: token.brand.base };
  const color = colors[status] || colors.idle;
  const shouldPulse = pulse && (status === "live" || status === "active" || status === "running");
  return (
    <span style={{ width: size, height: size, borderRadius: "50%", background: color, display: "inline-block", boxShadow: shouldPulse ? `0 0 0 ${size / 2}px ${color}40` : undefined, animation: shouldPulse ? "statusPulse 2s ease-in-out infinite" : undefined }}>
      <style>{`@keyframes statusPulse { 0%, 100% { box-shadow: 0 0 0 0px ${color}60; } 50% { box-shadow: 0 0 0 ${size}px ${color}00; } }`}</style>
    </span>
  );
});
