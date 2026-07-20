# DESIGN SYSTEM V2 - ALGO22 TERMINAL

## Overview
A premium, modern, institutional-grade design system tailored for the Algo22 Trading Terminal.

## Color Palette

### Backgrounds
- **App Background:** `#080A0D`
- **Panels/Cards:** `#131722`
- **Borders/Dividers:** `#202938`

### Status & Feedback
- **Positive (Profit/Up/Success):** `#26A69A`
- **Negative (Loss/Down/Error):** `#EF5350`
- **Accent (Primary Brand/Links):** `#2962FF`
- **Warning:** `#FFB74D`

## Typography

### Font Families
- **Primary Interface:** `Inter`, sans-serif
- **Data/Numbers/Code:** `IBM Plex Mono`, monospace

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
  - Primary: `#2962FF` background, white text. Hover states dim the color slightly or increase brightness.
  - Secondary: `#131722` background, `#202938` border, white text.
- **Hover States:** All interactive elements must have a defined hover state (opacity change, background lighten, or slight transform).
- **Active States:** Subtle scale down (`scale(0.98)`).

### Feedback & States
- **Loading States:** Utilize skeleton loaders matching the exact dimensions of the expected content to prevent layout shift.
- **Empty States:** Every table, list, and dashboard must have a beautifully designed empty state with an icon, description, and primary call-to-action.
- **Success/Error States:** Clear, color-coded toast notifications using `#26A69A` and `#EF5350`.

## Implementation Directives
- **CSS Architecture:** TailwindCSS will be the primary utility framework, configured with these exact hex codes and typography settings.
- **Animations:** Subtle micro-animations for enhanced user experience (150ms ease-in-out transitions for colors and transforms).
