# DESIGN SYSTEM V2 - ALGO22 TERMINAL

> **Brand Identity Decision (recorded 2026-07-28)**
> The canonical accent is **Cyan `#00D4FF`** across all surfaces — landing page, authenticated terminal,
> admin panel, and desktop app. The previous reference to `#2962FF` (blue) as the primary accent
> was a legacy artifact of a pre-rebrand state; all divergent occurrences have been corrected.
> `C.blue = "#2962FF"` is retained in `primitives.jsx` as a *secondary utility alias only*.
> See implementation report for full rationale.

## Overview
A premium, modern, institutional-grade design system tailored for the Algo22 Trading Terminal.
Visual language: dark terminal aesthetic (Bloomberg/TradingView-adjacent), Inter body text,
JetBrains Mono for data/numbers, cyan accent for brand identity continuity from landing to product.

## Color Palette

### Backgrounds
- **App Background:** `#080A0E`
- **Panels/Cards:** `#0F1117`
- **Elevated Surfaces:** `#151821`
- **Borders/Dividers:** `#1E2530`

### Status & Feedback
- **Positive (Profit/Up/Success):** `#26A69A` (teal-green)
- **Negative (Loss/Down/Error):** `#EF5350`
- **Accent (Primary Brand/Links):** `#00D4FF` ← canonical cyan
- **Warning:** `#FFB74D`
- **Secondary Blue (utility only):** `#2962FF` — not for brand accent

## Typography

### Font Families
- **Primary Interface:** `Inter`, sans-serif
- **Data/Numbers/Code:** `JetBrains Mono` (fallback: IBM Plex Mono, Fira Code), monospace

### Scale & Hierarchy
- **H1 (Dashboard Titles):** 24px, Font Weight 700
- **H2 (Section Headers):** 18px, Font Weight 600
- **H3 (Panel Titles):** 14px, Font Weight 600
- **Body:** 13px, Font Weight 400
- **Small Data/Tags:** 11px, Font Weight 500

## UI Components & Rules

### Containers
- **Border Radius:** Consistent `8px` (`0.5rem`) across all panels, buttons, and inputs.
- **Spacing:** Based on a 4px/8px grid. Panels use 16px or 24px internal padding.
- **Shadows:** Subtle dark shadows to lift panels off the dark background.
  - Default panel shadow: `0 4px 6px -1px rgba(0, 0, 0, 0.5)`

### Interactive Elements
- **Buttons:**
  - Primary: `#00D4FF` background, `#080A0E` dark text. Hover changes background and border only — no glow (see **Calm by default** below).
  - Secondary: `#151821` background, `#1E2530` border, white text.
- **Hover States:** All interactive elements must have a defined hover state (opacity change, background lighten, or slight transform).
- **Active States:** Subtle scale down (`scale(0.98)`).

### Feedback & States
- **Loading States:** Utilize skeleton loaders matching the exact dimensions of the expected content to prevent layout shift.
- **Empty States:** Every table, list, and dashboard must have a beautifully designed empty state with an icon, description, and primary call-to-action.
- **Success/Error States:** Clear, color-coded toast notifications using `#26A69A` and `#EF5350`.

## Implementation Directives
- **CSS Architecture:** TailwindCSS will be the primary utility framework, configured with these exact hex codes and typography settings.
- **Token Source of Truth:** `src/styles/tokens.css` (Tailwind v4 `@theme`) is the sole source of truth. `src/design/tokens.js` is generated from it. `C` in `ui-legacy/primitives.jsx` is a derived compatibility shim scheduled for deletion — never import it into new code.
- **Calm by default:** Colour, border emphasis and elevation are spent **only** on elements that represent (a) current state, (b) risk, or (c) an action the trader can take. Everything else is `content-primary` / `content-secondary` on `surface-panel`. `C.glow` and `C.gradient` are **retired** — every key resolves to `none`. Motion is kept only where it communicates state rather than decorating it: the connection dot's slow opacity pulse while connected, skeleton shimmer while loading, and 120–180ms colour/opacity transitions on interactive elements.

## Accessibility

Figures corrected 2026-09-09 — the previously stated 7.94:1 and 14.2:1 were measurement errors.
Both corrected values still pass AAA, so no colour changes were needed.

- `#00D4FF` on `#080A0E`: contrast ratio **11.2:1** — passes WCAG AA and AAA ✅
- `#F0F2F5` on `#0F1117`: contrast ratio **16.8:1** — passes WCAG AAA ✅
- `#5A6578` on `#0F1117`: contrast ratio **3.2:1** — **NON-TEXT ONLY.** Borders, dividers, disabled affordances and decorative icons. Never body copy, labels, or figures.
