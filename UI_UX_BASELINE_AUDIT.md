# VYOMQUANT — UI/UX BASELINE AUDIT & MODERNIZATION BLUEPRINT

**Classification:** UI/UX DESIGN SYSTEM & PRODUCT USABILITY AUDIT (READ-ONLY BASELINE)
**Date:** 2026-08-30
**Lead UI/UX Architect:** Principal Quantitative Frontend & Product Design Engineer (Antigravity)
**Scope:** Frontend Presentation, Layout, Typography, Component Hierarchy, Responsive Behavior, Usability & Micro-Interactions across all 13 Product Surfaces.

---

## 1. EXECUTIVE SUMMARY

This audit establishes the baseline architectural state of the VYOMQUANT frontend across design tokens, global application shell, layout systems, component primitives, and all 13 core product pages.

### Core Objective:
Transform VYOMQUANT from a functional yet visually fragmented trading interface into an **institutional-grade, calm, high-density, trustworthy, and modern quantitative trading SaaS platform**.

### Strict Scope Boundary:
- **100% UI/UX Focus:** Visual design, layout, spacing, typography, colors, component hierarchy, navigation, responsive behavior, interaction design, loading/empty/error states, and accessibility.
- **0% Functional Modification:** Zero changes to backend business logic, API request/response contracts, Supabase authentication/MFA/AAL2, RBAC, tenant isolation, database schemas, trading execution, paper trading simulator, strategy compiler/validator semantics, VectorBT backtest math, or risk calculations.

---

## 2. IMMUTABLE BASELINE GIT STATE

```text
Git Commit HEAD: f0e4fc6 feat: complete trading-lifecycle-integration spec
Recent Commits:
  - f0e4fc6: feat: complete trading-lifecycle-integration spec
  - af977d2: fix(telemetry): repair QuestDB schema bootstrap that silently created nothing
  - 7d8093c: chore(db): add auditable runner used to apply migration 007 to production
  - 1497d35: fix(db): add missing marketplace columns to library_strategies (PostgreSQL 42703)
  - e1424da: fix(frontend): remove dev-only telemetry shims and test artifact from production HTML
Working Tree: Clean to authorized Phase 8 public landing change set (0 unauthorized changes)
```

---

## 3. FRONTEND ARCHITECTURE & SHELL INVENTORY

### Routing & Navigation Tree:
```
/ (LandingPage)
├── /signin (AuthPage - mode="signin")
├── /signup (AuthPage - mode="signup")
├── /download (DownloadPage)
├── /marketplace (StrategyMarketplace - Public)
├── /legal/* (LegalPageRoute: terms, privacy, risk, refund)
├── /reset-password (UpdatePasswordPage)
├── /2fa (TwoFA)
├── /wizard (Wizard)
└── /app/* (AuthGuard ──> AppShell Layout)
       ├── /app/dashboard (Dashboard)
       ├── /app/strategies (Strategies)
       ├── /app/strategies/:id (StrategyDetail)
       ├── /app/signal-trace (SignalTrace)
       ├── /app/signal-trace/:id (SignalTrace)
       ├── /app/builder (StrategyBuilder - ReactFlow DAG Canvas)
       ├── /app/backtest (Backtester)
       ├── /app/marketplace (StrategyMarketplace)
       ├── /app/exchange (ExchangeManager)
       ├── /app/risk (RiskSettings)
       ├── /app/billing (Billing)
       ├── /app/profile (Profile)
       ├── /app/security-logs (SecurityLogs)
       ├── /app/portfolio (Portfolio)
       ├── /app/trades (TradeHistory)
       ├── /app/notifications (NotificationCenter)
       └── /app/support (SupportCenter)
```

### Global Application Shell (`AppShell`):
- **Sidebar (`Sidebar.jsx`):** Left navigation dock (width 220px / 60px collapsed) with 3 logical groups (*Command Center*, *Vault*, *Platform*), dynamic unread badge counter, and authenticated user tier badge.
- **TopBar (`TopBar.jsx`):** Upper status bar (height 44px) with live system status indicator (`LiveStatusV2`), real-time UTC clock, interactive notification trigger, and user profile avatar.
- **Desktop Only Overlay (`DesktopOnlyOverlay.jsx`):** Enforces minimum viewport width for complex terminal operations while permitting responsive mobile browsing on non-canvas surfaces.
- **Toast System (`ToastContainer`):** Global event notification queue (`window.showToast`).

---

## 4. CURRENT DESIGN TOKENS & SYSTEM GAPS

### Current Color Palette Fragmentation:
The codebase currently exhibits slight variations of dark palette definitions across legacy and modern components:
1. `index.css` Tailwind Theme: `--color-bg-0: #080A0E`, `--color-bg-surface: #0F1117`, `--color-bg-elevated: #151821`, `--color-accent-cyan: #00D4FF`.
2. `primitives.jsx` Legacy Token Object: `C.bg0: #080A0E`, `C.bg1: #0F1117`, `C.border: #1E2530`.
3. `NotificationCenter.jsx` Token Object: `C.bg: #0a0a0a`, `C.bg2: #0f1115`, `C.border: #1f2937`.
4. `SupportCenter.jsx` Token Object: `C.bg: #010608`, `C.border: #0f2035`.

### Standardized Institutional Token System:
```css
/* Core Institutional Backgrounds */
--bg-canvas: #080A0E;        /* Deepest base layer */
--bg-surface: #0C1017;       /* Primary card & section surface */
--bg-elevated: #131923;      /* Elevated dropdowns, modals, popovers */
--bg-subtle: #18202C;        /* Hover and active row highlights */

/* Structural Borders */
--border-subtle: #161D27;    /* Low-contrast divider */
--border-default: #1F2A38;   /* Standard card & container border */
--border-active: #2C3B4E;    /* Interactive focus and active tab border */

/* Text Hierarchy */
--text-primary: #F0F4F8;     /* High-contrast headings and active metrics */
--text-secondary: #94A3B8;   /* Standard readable body and labels */
--text-muted: #5A697E;       /* Captions, timestamps, disabled items */

/* Trading Semantics */
--color-profit: #10B981;     /* Gains, long signals, valid status */
--color-profit-dim: rgba(16, 185, 129, 0.12);
--color-loss: #EF4444;       /* Losses, short signals, errors */
--color-loss-dim: rgba(239, 68, 68, 0.12);
--color-cyan: #00D4FF;       /* Quantitative accent, links, active state */
--color-cyan-dim: rgba(0, 212, 255, 0.12);
--color-gold: #F59E0B;       /* Warnings, caution states, pending approval */
--color-gold-dim: rgba(245, 158, 11, 0.12);
```

---

## 5. PAGE-BY-PAGE UX AUDIT (ALL 13 SURFACES)

### 1. Landing Page (`LandingPage.jsx`)
- **Current State:** Redesigned in Phase 8D with dominant headline, interactive 4-tab Architecture Showcase, systematic 4-step pipeline, and fallback pricing tiers.
- **Identified UX Opportunities:**
  - Enhance visual transition alignment between public landing styling and authenticated terminal shell.
  - Refine subtle micro-interactions on the DAG terminal demo.

### 2. Sign In & Sign Up (`AuthPage.jsx`, `TwoFA.jsx`, `UpdatePasswordPage.jsx`)
- **Current State:** Hardened in Phase 7 with zxcvbn password strength meter, email confirmation flow, MFA challenge, and password reset handler.
- **Identified UX Opportunities:**
  - Polish input field focus ring transitions.
  - Upgrade password strength score badge with clearer visual feedback.
  - Refine mobile vertical centering to prevent soft-keyboard displacement.

### 3. Dashboard (`Dashboard.jsx`, `Portfolio.jsx`)
- **Current State:** Comprehensive overview with portfolio metrics, equity curve, active bot status, and signal feed.
- **Identified UX Opportunities:**
  - High information density can feel visually crowded without distinct card spacing hierarchy.
  - Standardize KPI cards with subtle border treatments and crisp monospace typography for monetary figures.
  - Refine empty states for accounts with 0 connected exchanges or 0 active bots.

### 4. Strategies Page (`Strategies.jsx`, `StrategyDetail.jsx`)
- **Current State:** Strategy list with status filtering (Draft, Validated, Backtested, Deployed), version badges, and creation modal.
- **Identified UX Opportunities:**
  - Improve status badge visual contrast (e.g. `DEPLOYED` vs `DRAFT` vs `STOPPED`).
  - Upgrade empty state illustration when 0 strategies exist.
  - Streamline strategy card action menus (Clone, Rename, Archive, Deploy).

### 5. Signal Trace (`SignalTrace.jsx`)
- **Current State:** Quantitative observability timeline with signal payload inspection and trade execution details.
- **Identified UX Opportunities:**
  - Signal payload JSON tree can be dense and intimidating.
  - Add progressive disclosure: summary chips for indicator parameters + expandable raw telemetry inspector.
  - Improve timeline date-range filter UI.

### 6. Strategy Builder (`StrategyBuilder.jsx`, `NodePreview.jsx`, `NodeTrace.jsx`)
- **Current State:** ReactFlow canvas with 7 category palette, node inspector, debounced 400ms validation markers, and preflight deploy panel.
- **Identified UX Opportunities:**
  - Node design can be elevated with crisp category accent borders and clearer port handle hit areas.
  - Selected node state in canvas should feature a distinct focus halo.
  - Minimap and canvas controls can be docked with cleaner semi-transparent glass styling.
  - Validation issue drawer should render severity badges and fix hints with instant jump-to-node buttons.

### 7. Backtester (`Backtester.jsx`)
- **Current State:** VectorBT backtesting parameters, parameter sweep inputs, equity curve chart, and performance metrics table.
- **Identified UX Opportunities:**
  - Reorganize layout into a clean 2-column workflow: Left panel for configuration/inputs, Right panel for high-density results.
  - Improve chart tooltips with formatted currency and percentage labels.
  - Format metrics grid (Sharpe, Sortino, Max Drawdown, Win Rate, Profit Factor) into distinct quantitative statistic blocks.

### 8. Billing (`Billing.jsx`)
- **Current State:** Current plan card, usage meters (bot slots, backtests), tier comparison cards, invoice history, and referral program link.
- **Identified UX Opportunities:**
  - Modernize plan comparison cards with clear feature checklists.
  - Usage progress bars should feature smooth gradient fills and percentage text.
  - Payment status badges should clearly communicate billing cycle dates.

### 9. Notifications (`NotificationCenter.jsx`)
- **Current State:** Real-time event feed with category filters (Trading, Security, Billing, System), search, and read/unread toggle.
- **Identified UX Opportunities:**
  - Modernize notification item cards with category-specific icon badges.
  - Add quick action buttons (e.g. "View Signal", "Manage Key", "Dismiss") directly on hover.
  - Enhance empty state when all notifications are cleared.

### 10. Risk Settings (`RiskSettings.jsx`)
- **Current State:** Portfolio safety controls, killswitch toggle, max drawdown limit, leverage ceiling, and position size constraints.
- **Identified UX Opportunities:**
  - Highlight dangerous controls with prominent caution borders and two-step confirmation styling.
  - Clearly distinguish current active value vs draft/editable input field.
  - Add contextual tooltips explaining mathematical impact of risk parameters.

### 11. Profile & Security (`Profile.jsx`, `SecurityLogs.jsx`)
- **Current State:** User profile details, connected exchange accounts list, 2FA status, active sessions, and security audit log.
- **Identified UX Opportunities:**
  - Clean up section tabs (Account, Security, Exchanges, Preferences).
  - Modernize security logs table with monospace IP/timestamp formatting.
  - Enhance 2FA status card with clear visual toggle indicator.

### 12. Support Center (`SupportCenter.jsx`)
- **Current State:** Ticket creation form, active tickets table, categorized FAQ accordion, and search.
- **Identified UX Opportunities:**
  - Refine ticket status tags (`OPEN`, `WAITING ON YOU`, `RESOLVED`).
  - Improve ticket conversation thread layout with distinct user vs support bubbles.
  - Make FAQ accordion categories easily filterable with interactive pill buttons.

### 13. Marketplace (`StrategyMarketplace.jsx`)
- **Current State:** Public and authenticated strategy library with category filters, author attribution, and clone CTA.
- **Identified UX Opportunities:**
  - Standardize marketplace strategy cards with institutional quantitative badges (Sharpe, Timeframe, Asset class).
  - Improve search bar and tag filter ergonomics.

---

## 6. SYSTEMATIC 5-STAGE MODERNIZATION IMPLEMENTATION PLAN

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ STAGE 1: DESIGN SYSTEM & FOUNDATION                                         │
│ └── Unified tokens in index.css, Tailwind classes, standard buttons,        │
│     cards, inputs, badges, skeletons, and AppShell (Sidebar & TopBar).      │
├─────────────────────────────────────────────────────────────────────────────┤
│ STAGE 2: CORE TRADING SURFACES                                              │
│ └── Dashboard, Strategies, Strategy Builder, Backtester, Signal Trace.      │
├─────────────────────────────────────────────────────────────────────────────┤
│ STAGE 3: ACCOUNT & SECURITY SURFACES                                        │
│ └── Sign In, Sign Up, Profile, Risk Settings, Notification Center.          │
├─────────────────────────────────────────────────────────────────────────────┤
│ STAGE 4: COMMERCIAL, SUPPORT & MARKETPLACE                                  │
│ └── Billing, Support Center, Strategy Marketplace.                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ STAGE 5: PUBLIC LANDING & VERIFICATION                                      │
│ └── Landing Page alignment, Vitest (100%), Build (100%), Pytest (100%).     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. FUNCTIONAL PRESERVATION & INTEGRITY GUARANTEE

During this entire program:
- **Zero modification to backend code or API schemas.**
- **Zero modification to Supabase authentication, JWTs, or MFA.**
- **Zero modification to live execution engine or safety circuit breakers.**
- **Zero fabrication of mock data or simulated numbers.**

---

## 8. DELIVERABLE LOCATION

The authoritative baseline report is saved to:
[`UI_UX_BASELINE_AUDIT.md`](file:///C:/aerora_quant_backend_updated_final1/UI_UX_BASELINE_AUDIT.md)

---
*End of UI_UX_BASELINE_AUDIT.md*
