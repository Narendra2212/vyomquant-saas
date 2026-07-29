# VyomQuant — Landing Page Architecture V1

> **Document Type:** UX Architecture  
> **Role:** Principal Product Designer / SaaS Conversion  
> **Date:** 2026-06-22  
> **Platform:** VyomQuant (vyomquant.linkpc.net)  
> **Status:** Design-only — no code  

---

## Table of Contents

1. [Strategic Brief](#1-strategic-brief)
2. [Target User Taxonomy](#2-target-user-taxonomy)
3. [Competitive Positioning](#3-competitive-positioning)
4. [Conversion Funnel Model](#4-conversion-funnel-model)
5. [Complete Landing Page Structure & Section Order](#5-complete-landing-page-structure--section-order)
6. [Section Blueprints](#6-section-blueprints)
7. [Hero Design Specification](#7-hero-design-specification)
8. [CTA Placement Strategy](#8-cta-placement-strategy)
9. [Trust Architecture](#9-trust-architecture)
10. [Pricing Presentation](#10-pricing-presentation)
11. [Feature Hierarchy](#11-feature-hierarchy)
12. [FAQ Architecture](#12-faq-architecture)
13. [Mobile Layout System](#13-mobile-layout-system)
14. [Design Tokens & Visual Language](#14-design-tokens--visual-language)
15. [Navigation System](#15-navigation-system)
16. [Micro-Animation Strategy](#16-micro-animation-strategy)
17. [SEO & Metadata Specification](#17-seo--metadata-specification)
18. [Open Questions for Stakeholder Review](#18-open-questions-for-stakeholder-review)

---

## 1. Strategic Brief

### Mandate

Convert anonymous visitors to VyomQuant into activated free-tier users and, within a 14-day adoption window, into paying Pro subscribers. The landing page is the sole gateway between `vyomquant.linkpc.net` and the authenticated terminal.

### Core Tension to Resolve

VyomQuant competes in a market where:
- Institutional tools (QuantConnect, custom Python) are extremely powerful but inaccessible to retail traders.
- Retail tools (Tradetron, AlgoBulls) are accessible but feel low-trust and toy-like.

**VyomQuant's whitespace:** Institutional-grade infrastructure with a no-code UX. The landing page must communicate *both* simultaneously without compromising either.

### Conversion Goal Hierarchy

| Priority | Goal |
|---|---|
| P0 | Free signup completion (email + password) |
| P1 | Demo booking for institutional-scale users |
| P2 | Organic strategy marketplace exploration (social proof loop) |

---

## 2. Target User Taxonomy

Each persona requires a different emotional trigger on the landing page. The UX must serve all five without diluting the premium signal.

| Persona | Job-To-Be-Done | Fear | Key Trigger |
|---|---|---|---|
| **Retail Trader** | Automate manual rules without hiring a developer | "I'll lose money on bad code" | No-code safety, free tier, live paper mode |
| **Crypto Trader** | Deploy bots across multiple exchanges 24/7 | "Downtime = missed opportunities" | Multi-exchange CCXT support, 99.9% uptime, real-time WebSocket |
| **Options Trader** | Backtest complex structured strategies | "Historical data is unreliable" | VectorBT precision, tick-level backtesting, Sharpe/Sortino reporting |
| **Quant Trader** | Integrate ML models into execution pipeline | "No-code tools can't handle ML workflows" | XGBoost node, ML training slots, DAG compiler, signal trace |
| **Strategy Developer** | Publish and monetise strategies | "No distribution channel exists" | Strategy Marketplace, clone metrics, author visibility |

### Primary Design Audience (Above-the-Fold Optimisation)

The hero section is optimised primarily for **Retail Traders** and **Crypto Traders** (widest addressable market). Quant and Options trader trust signals are introduced progressively deeper in the page.

---

## 3. Competitive Positioning

### Competitor Weakness Matrix

| Competitor | Primary Weakness | VyomQuant Counter |
|---|---|---|
| **Tradetron** | India-only exchange support, dated UI | Multi-exchange (CCXT.pro), global, institutional dark UI |
| **AlgoTest** | Backtesting-only, no live execution | Full stack: backtest → paper → live in one platform |
| **QuantConnect** | Python-only, 30-min learning curve per feature | Visual DAG builder, zero code required |
| **AlgoBulls** | No ML integration, limited risk controls | XGBoost node, kill-switch system, AES-256 vault |
| **Composer Trade** | US equities only, no real-time WebSocket | Crypto + equity, live WebSocket, real-time signal trace |

### Positioning Statement (Used in Hero)

> "Build, test, and deploy algorithmic trading strategies — without writing a single line of code."

### Unique Value Proposition Pillars (Used Across Page)

1. **No-Code, Institutional-Grade** — DAG compiler with ML nodes, AES-256 vault, kill-switch system
2. **Backtest → Paper → Live** — Single platform, one click deployment mode switch
3. **Strategy Marketplace** — Discover, clone, and rate community strategies
4. **Real-Time Everything** — WebSocket-native: ticker, orderbook, P&L, candles, user fills

---

## 4. Conversion Funnel Model

```
AWARENESS
    ↓
[S01 — NAV BAR]         ← sticky, always visible, CTA always present
    ↓
[S02 — HERO]            ← 70% of conversion decision made here
  ├── Primary CTA: "Start Free — No Card Required"  → Signup
  └── Secondary CTA: "Book Private Demo"            → Demo Form
    ↓
[S03 — SOCIAL PROOF BAR] ← credibility injection, reduces bounce
    ↓
[S04 — PRODUCT PREVIEW]  ← terminal screenshot / animation
    ↓
[S05 — FEATURE GRID]     ← answers "can it actually do X?"
    ↓
[S06 — HOW IT WORKS]     ← removes friction for hesitant users
    ↓
[S07 — STRATEGY MARKETPLACE TEASER] ← FOMO loop
    ↓
[S08 — PRICING]          ← anchoring + tier selection
    ↓
[S09 — TRUST & SECURITY] ← risk mitigation for financial platform
    ↓
[S10 — TESTIMONIALS]     ← social proof from similar users
    ↓
[S11 — FAQ]              ← objection handling
    ↓
[S12 — FINAL CTA BAND]   ← last chance conversion before footer
    ↓
[S13 — FOOTER]           ← legal, links, trust links
```

### Drop-off Mitigation

| Funnel Stage | Risk | Mitigation |
|---|---|---|
| Hero → Features | User doesn't understand what it does | Product preview visual immediately after hero |
| Features → Pricing | Price shock | Feature-anchoring before pricing; free tier shown first |
| Pricing → Signup | Trust deficit on financial platform | Security section immediately follows pricing |
| FAQ → Exit | Unresolved objection | FAQ is comprehensive; exit-intent handled by final CTA band |

---

## 5. Complete Landing Page Structure & Section Order

```
┌─────────────────────────────────────────────────────────────────┐
│  S01 — STICKY NAVIGATION BAR                                    │
│  Logo · Features · Pricing · Marketplace · Docs · Sign In      │
│  CTA: "Start Free"                                              │
├─────────────────────────────────────────────────────────────────┤
│  S02 — HERO SECTION                                             │
│  Primary headline + sub-headline + dual CTA + trust badges      │
├─────────────────────────────────────────────────────────────────┤
│  S03 — SOCIAL PROOF / METRICS BAR                               │
│  Animated counters: Users · Strategies · Backtests · Exchanges  │
├─────────────────────────────────────────────────────────────────┤
│  S04 — PRODUCT PREVIEW (INTERACTIVE)                            │
│  Tabbed view of Strategy Builder / Backtesting / Monitoring     │
├─────────────────────────────────────────────────────────────────┤
│  S05 — CORE FEATURE GRID (Tier 1 Features)                      │
│  6-card grid: Visual Builder, Backtesting, Paper Trading,       │
│  ML Nodes, Risk Controls, Strategy Marketplace                  │
├─────────────────────────────────────────────────────────────────┤
│  S06 — HOW IT WORKS (3-Step Flow)                               │
│  1. Build  →  2. Backtest & Validate  →  3. Deploy              │
├─────────────────────────────────────────────────────────────────┤
│  S07 — STRATEGY MARKETPLACE TEASER                              │
│  Live community strategies preview + Clone CTA                  │
├─────────────────────────────────────────────────────────────────┤
│  S08 — PRICING SECTION                                          │
│  3-tier cards: Free / Pro ($12) / Elite ($24)                   │
│  Annual toggle with 20% savings badge                           │
├─────────────────────────────────────────────────────────────────┤
│  S09 — TRUST & SECURITY SECTION                                 │
│  AES-256 Vault · RLS · Kill Switch · Exchange Isolation         │
├─────────────────────────────────────────────────────────────────┤
│  S10 — TESTIMONIALS                                             │
│  Carousel / 3-col grid: Quant · Crypto Trader · Asset Manager   │
├─────────────────────────────────────────────────────────────────┤
│  S11 — FAQ ACCORDION                                            │
│  8 questions across 2 categories: Platform · Billing            │
├─────────────────────────────────────────────────────────────────┤
│  S12 — FINAL CTA BAND                                           │
│  "Ready to deploy your first strategy?" + "Start Free" CTA      │
├─────────────────────────────────────────────────────────────────┤
│  S13 — FOOTER                                                   │
│  Logo · Nav links · Legal · Social · Copyright                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. Section Blueprints

### S01 — Sticky Navigation Bar

**Visual Weight:** Minimal. Glass-morphism background with backdrop blur. Always visible on scroll.

**Layout:** `[Logo] ........................ [Nav Links] [Sign In] [Start Free →]`

**Nav Items:**
- Features (anchor scroll to S05)
- Pricing (anchor scroll to S08)
- Marketplace (anchor scroll to S07)
- Docs (external link to docs subdomain)
- Sign In (link to auth page)

**Behaviour on Scroll:**
- At top: transparent background, no border
- After 80px scroll: frosted glass background (`rgba(dark, 0.8)`) + bottom border appears
- Active section highlight on nav items (scroll-spy)

**Mobile:** Hamburger menu. CTA collapses into hamburger at breakpoint `< 768px`.

---

### S02 — Hero Section

*See Section 7 for full specification.*

---

### S03 — Social Proof / Metrics Bar

**Purpose:** Credibility injection. Reduces post-hero bounce rate. No selling — pure signal.

**Layout:** Full-width horizontal bar. 4 animated stat counters in a row, separated by vertical dividers.

**Stats:**
| Stat | Display Value | Label |
|---|---|---|
| Active Traders | `12,400+` | Traders on Platform |
| Strategies Built | `38,000+` | Strategies Created |
| Backtests Run | `2.1M+` | Backtests Executed |
| Exchanges Supported | `50+` | Exchanges via CCXT |

**Interaction:** On-scroll entry: count-up animation fires once when section enters viewport. Values animate from 0 to final value over 1.2s with easing.

**Visual:** Dark tinted strip (slightly lighter than hero background). Monospace font for the numbers. Subtle separator lines between items.

---

### S04 — Product Preview (Interactive Tab Panel)

**Purpose:** Show — don't tell. The single biggest conversion lever on any SaaS landing page. Users need to *see* the terminal before they trust it.

**Layout:** Large centred panel (max-width 1100px) with 3 tabs above it.

**Tabs:**

| Tab | Content Shown | Key Elements |
|---|---|---|
| **Strategy Builder** | Node-graph canvas screenshot/animation | DAG nodes: Market Data → Indicator → Logic → Action |
| **Backtest Results** | Equity curve chart + stats grid | Sharpe ratio, Win rate, Max drawdown, Total return |
| **Live Monitoring** | Bot monitor console screenshot | Running bots, P&L, signal trace panel, kill switch |

**Tab Switching:** Animated fade + subtle slide transition. Active tab indicated by accent-colour underline.

**Visual Frame:** Terminal-style dark chrome frame around the preview panel. Faint scanline texture. Blinking cursor in the top status bar. This communicates institutional precision.

**Mobile:** Tabs collapse to a vertical accordion. One preview shown at a time.

---

### S05 — Core Feature Grid

*See Section 11 for Feature Hierarchy specification.*

---

### S06 — How It Works (3-Step Flow)

**Purpose:** Remove friction for hesitant users who understand the value but are uncertain about the workflow. Answers "where do I even start?"

**Layout:** Horizontal 3-step stepper with connecting line. Full-width, centred max-width 900px.

**Steps:**

```
[ STEP 1 ]          →          [ STEP 2 ]          →          [ STEP 3 ]
  Build                          Validate                        Deploy
──────────                     ──────────                      ──────────
Drag-and-drop                  Backtest across              One-click deploy
indicators and                 2 years of tick             as live or paper
entry/exit logic.              data. Run Monte             bot. Monitor in
No code.                       Carlo scenarios.            real-time.
```

**Visual Details:**
- Each step enclosed in a numbered circle (accent colour) connected by dashed horizontal line
- Icon above each step: `[GitBranch]` · `[BarChart2]` · `[Rocket]`
- On hover: Step card elevates with glow shadow
- Connecting line animates left to right on scroll entry (draw-on effect)

---

### S07 — Strategy Marketplace Teaser

**Purpose:** Introduce the Strategy Library as a network-effect moat. This creates FOMO for solo traders — they want access to 38,000+ community strategies.

**Layout:** Full-width dark band. Left side: copy block. Right side: 2×2 grid of strategy cards (styled exactly like the in-app Marketplace).

**Strategy Cards Shown (illustrative — from existing mock data):**

| Card | Name | Metric 1 | Metric 2 |
|---|---|---|---|
| A | RSI Mean Reversion Alpha | Sharpe: 2.1 | 30D: +14.5% |
| B | EMA Golden Cross Trend | Sharpe: 1.5 | 12,450 copiers |
| C | ML Hybrid Breakout | Sharpe: 3.8 | 30D: +19.2% |
| D | Grid Market Making | Sharpe: 3.4 | 8,320 copiers |

**CTAs in this section:**
- "Explore Strategy Library →" (secondary/ghost style)
- "Clone a Strategy Free →" (primary, routes to signup)

**Trust Copy Below Grid:** *"Publish your own. Build reputation. The community is watching."*

---

### S08 — Pricing Section

*See Section 10 for full specification.*

---

### S09 — Trust & Security Section

**Purpose:** Financial platforms live and die on trust. This section directly addresses the highest-anxiety objection: "Is my money and API key safe?"

**Layout:** 2-column layout. Left: headline copy block. Right: 4 trust cards in a 2×2 grid.

**Trust Pillars:**

| Icon | Title | Copy |
|---|---|---|
| `[Lock]` | AES-256 Credential Vault | Exchange API keys encrypted at rest. Secrets never leave the backend execution path. |
| `[Shield]` | Row-Level Security (RLS) | Supabase PostgreSQL RLS policies enforce strict user-data isolation. Your data is yours. |
| `[Power]` | Instant Kill Switch | One-click emergency stop for all deployed bots. Automatic kill on position drift detection. |
| `[Key]` | Exchange Isolation | Your keys never touch shared memory. Separate encrypted context per tenant per request. |

**Additional Trust Signals in this section:**
- "Sentry monitored — 99.9% uptime SLA" badge
- "Paper-trading sandbox — risk-free testing by default" badge
- "No live execution without your explicit consent" badge

---

### S10 — Testimonials

*See Section 9 (Trust Architecture) for context.*

**Layout:** 3-column card grid. On mobile: horizontal swipe carousel.

**Persona Coverage:**
- Card 1 (Quant Trader): Speaks to ML model integration and DAG precision
- Card 2 (Crypto Trader): Speaks to 24/7 uptime and paper trading accuracy
- Card 3 (Asset Manager): Speaks to institutional risk controls

**Visual:** Star rating row (5 stars, gold) above quote. Monogram avatar. Author name + role below.

---

### S11 — FAQ Accordion

*See Section 12 for full specification.*

---

### S12 — Final CTA Band

**Purpose:** Last conversion opportunity before footer. Users who reach this point are interested but have not yet acted.

**Layout:** Full-width, visually distinct from the rest of the page. Uses radial gradient background with accent glow. Centred content.

**Copy:**
- **Headline:** "Your First Automated Strategy Is One Click Away."
- **Sub-headline:** "Join 12,400+ traders building smarter systems on VyomQuant."
- **Primary CTA:** "Start Free — No Credit Card Required" (large, pulsing)
- **Secondary:** "Book a Private Demo →"

**Visual:** Animated particle or grid background (CSS-only, no JS library). Headline text has subtle gradient fill (white → accent cyan).

---

### S13 — Footer

**Layout:** 4-column footer on desktop. Stacked single-column on mobile.

**Columns:**
1. **Brand**: Logo + tagline + copyright
2. **Platform**: Features · Pricing · Strategy Marketplace · Docs · Status
3. **Company**: About · Blog · Careers · Security · API
4. **Legal**: Terms of Service · Privacy Policy · Risk Disclosure · Refund Policy

**Footer Bottom Bar:** `© 2026 VyomQuant. Algorithmic trading involves risk. Paper trading mode enabled by default. Past performance does not guarantee future results.`

**Risk Disclaimer:** Required regulatory statement displayed prominently in footer. Monospace, small, muted colour. Not hidden.

---

## 7. Hero Design Specification

### Layout Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        S01 NAV (sticky above)                            │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│              [Eyebrow Label — Pill Badge]                                │
│        "Now in Beta · Strategy Marketplace Live"                         │
│                                                                          │
│                     [ H1 HEADLINE ]                                      │
│         Build, Backtest, and Deploy Crypto Trading                       │
│              Strategies — Without Writing Code.                          │
│                                                                          │
│                   [ SUB-HEADLINE ]                                       │
│    VyomQuant is the institutional-grade no-code platform for             │
│    algorithmic traders. Visual DAG builder, ML model nodes,             │
│    VectorBT backtesting, and live WebSocket execution in one terminal.   │
│                                                                          │
│            [ CTA PRIMARY ]     [ CTA SECONDARY ]                         │
│         "Start Free →"         "Book Private Demo"                       │
│                                                                          │
│         ──────────────────────────────────────────                       │
│                  [ TRUST BADGE ROW ]                                     │
│         ✓ No credit card    ✓ Paper mode default    ✓ 50+ exchanges      │
│                                                                          │
│                    [ HERO VISUAL PANEL ]                                 │
│             (terminal preview, glowing edge, floating cards)             │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### Hero Visual Treatment

**Background:**
- Base: Deep near-black (`#080A0E` equivalent)
- Radial gradient: Accent cyan emitting from top-center, fading to transparent at 60% height
- Grid overlay: Fine 56px CSS grid lines at very low opacity (4–8%)
- Gradient fade to page background at bottom

**H1 Headline:**
- Font: Inter, weight 900, tracked tightly (`letter-spacing: -0.04em`)
- Size: Fluid — `clamp(2.6rem, 6.5vw, 5.2rem)`
- Colour: Near-white (`#F8FAFC`)
- "Without Writing Code." — This phrase rendered in accent cyan gradient to visually anchor the no-code differentiator

**Sub-Headline:**
- Font: Inter, weight 400
- Size: `clamp(0.9rem, 1.5vw, 1.05rem)`
- Colour: Medium muted (`#94A3B8` equivalent)
- Max-width: 680px centred

**CTA Button Primary:**
- Solid accent colour fill
- Text: Black (`#000`) for contrast
- Shadow: Accent glow + depth shadow
- Hover: Translate Y(-2px) + intensified glow
- Animation: Subtle 3s pulse glow loop at idle state (breathing effect)
- Label: **"Start Free — No Card Required"**

**CTA Button Secondary:**
- Transparent background, border: `1px solid border-colour`
- Text: Near-white
- Hover: Background tint fills in
- Label: **"Book Private Demo"**

**Hero Visual Panel (Below CTAs):**
- Large browser/terminal chrome frame
- Shows the Strategy Builder DAG canvas OR the monitoring dashboard
- Floating "performance chip" cards overlay the panel: `Sharpe: 2.1` · `Win Rate: 67.4%` · `30D: +14.5%`
- Panel has a subtle inner glow edge in accent colour
- Panel slightly clipped below the fold to invite scrolling

**Eyebrow Pill Badge (Above H1):**
- Background: `accent + 20% opacity`
- Border: `1px solid accent + 40% opacity`
- Text: Accent colour, monospace, uppercase
- Content: "✦ Strategy Marketplace Now Live — Explore 38,000+ Strategies"

---

## 8. CTA Placement Strategy

### CTA Inventory (All Instances)

| # | Location | CTA Label | Action | Style |
|---|---|---|---|---|
| 1 | Navigation Bar (sticky) | Start Free | → Signup | Primary solid |
| 2 | Hero — Primary | Start Free — No Card Required | → Signup | Primary solid + pulse |
| 3 | Hero — Secondary | Book Private Demo | → Demo Modal | Ghost |
| 4 | S03 Metrics Bar | (none — no CTA here) | — | — |
| 5 | S04 Product Preview | Try It Free → | → Signup | Inline link |
| 6 | S05 Feature Grid | (none per card — CTA is at section end) | — | — |
| 7 | S05 Feature Grid — bottom | Explore All Features | → Signup | Ghost |
| 8 | S06 How It Works — end | Build Your First Strategy | → Signup | Primary |
| 9 | S07 Marketplace Teaser | Browse Strategy Library | → Signup | Ghost |
| 10 | S07 Marketplace Teaser | Clone a Strategy Free → | → Signup | Primary |
| 11 | S08 Pricing — each card | Plan-specific CTA (see Pricing section) | → Signup | Varies |
| 12 | S09 Trust — end | Open Your Free Account | → Signup | Primary |
| 13 | S12 Final CTA Band | Start Free — No Credit Card Required | → Signup | Primary large + pulse |
| 14 | S12 Final CTA Band | Book a Private Demo → | → Demo Modal | Ghost |

### CTA Principles

1. **Never two primary CTAs at the same visual level.** Always one primary (solid) + one ghost per pairing.
2. **The signup action is always the primary CTA.** Demo booking is always secondary.
3. **CTA copy evolves down the page.** Top = generic ("Start Free"). Middle = feature-anchored ("Build Your First Strategy"). Bottom = urgency/social ("Join 12,400+ Traders").
4. **No CTA in pure trust/social proof sections** (S03, S09, S10). These sections build credibility; inserting a CTA here damages trust.
5. **Sticky nav CTA is always visible.** On mobile, it remains as an icon-only or short label.

---

## 9. Trust Architecture

### Trust Signal Layer Model

Trust is not a single section — it is a *layered system* distributed across the entire page.

```
Layer 1 — STRUCTURAL TRUST (Perceived Before Reading)
  - Dark, dense, institutional visual design
  - Monospace typography signals precision and technical competence
  - Real terminal chrome in product preview
  - Precise metrics with decimal places (Sharpe: 2.1, Win Rate: 67.4%)

Layer 2 — SOCIAL PROOF TRUST
  - S03 Metrics Bar: 12,400+ traders, 2.1M+ backtests
  - S07 Marketplace: 38,000+ strategies, clone counts
  - S10 Testimonials: Specific user roles (Asset Manager, Quant Trader)
  - Strategy cards showing real performance numbers

Layer 3 — SECURITY TRUST
  - S09 Trust Section: AES-256, RLS, Kill Switch, Tenant Isolation
  - "No live execution without consent" badge
  - Sentry monitoring badge
  - "Paper trading is the default" message

Layer 4 — LEGAL / REGULATORY TRUST
  - Footer risk disclosure
  - Terms of Service, Privacy Policy, Refund Policy links
  - "Beta platform" transparency — not hiding status

Layer 5 — INTERACTION TRUST
  - "No credit card required" repeated on both primary CTAs
  - Free tier is genuinely functional (1 paper bot, 3 backtests)
  - "Cancel anytime" in pricing copy
```

### Testimonial Specification

**3 testimonials. One per primary persona:**

| # | Author Persona | Quote Theme | Role |
|---|---|---|---|
| 1 | Quantitative Trader | "The visual DAG builder with XGBoost nodes gave me institutional-grade ML pipelines without the engineering overhead." | Quantitative Analyst, HNI Desk |
| 2 | Crypto Scalper | "Paper trading results match live. The kill switch saved me from a 3am exchange glitch wiping my position." | Crypto Scalper |
| 3 | Asset Manager / RIA | "The Monte Carlo and Walk Forward validation suite is institutional-grade. I use it before any live deployment." | Independent Asset Manager |

**Testimonial Card Anatomy:**
- 5 gold stars (fixed — all testimonials are 5-star on landing page)
- Block quote in italics
- Monogram avatar circle (accent-coloured)
- Author name (bold) + role (monospace, muted)

---

## 10. Pricing Presentation

### Pricing Philosophy

Anchor on value, not cost. The framing: "You're buying infrastructure previously only available to hedge funds."

The pricing section should feel *cheap relative to the alternative* (hiring a developer = $8,000+/month). Every tier must feel like a bargain for what it delivers.

### Tier Architecture

| Tier | Price | Positioning | Audience |
|---|---|---|---|
| **Free** | $0/month | Explore without commitment | New users, curious retail traders |
| **Pro** | $12/month | Serious automated trading | Active retail + crypto traders |
| **Elite** | $24/month | Machine learning pipeline | Quants, strategy developers |

### Pricing Card Anatomy

**Visual Hierarchy on Cards:**
1. Plan badge (for Pro: "Most Popular" in accent colour. For Elite: "ML Powered" in gold)
2. Plan name (largest text on card after price)
3. Plan descriptor (1-line positioning copy)
4. Price + "/month" unit (hero display size)
5. Annual toggle note (e.g., "Billed at $115.20/yr — save 20%")
6. Feature checklist (checkmarks in green accent)
7. CTA button (full width)

### Feature Comparison Per Tier

| Feature | Free | Pro | Elite |
|---|---|---|---|
| Deployed Paper Bots | 1 | 5 | Unlimited |
| Backtests / month | 3 | Unlimited | Unlimited |
| ML Model Training Slots | 0 | 0 | 3 |
| Indicators Access | Basic | All | All + Custom |
| Strategy Marketplace | Browse | Browse + Clone | Browse + Clone + Publish |
| Performance Analytics | Basic | Advanced | Advanced + Signal Trace |
| Support | Community | Email (48h) | Priority (SLA) |
| Live Execution | ❌ | ✅ | ✅ |

### Annual Toggle

- Toggle switch at top of pricing section
- Default: Monthly
- On toggle to Annual: all prices update, savings badge appears (20% discount)
- Micro-copy: "Save 2 months every year"

### Visual Differentiation Between Tiers

- **Free:** Neutral border, no badge, ghost CTA button
- **Pro:** Accent-colour border glow, "Most Popular" badge, solid primary CTA
- **Elite:** Gold border glow, "ML Powered" badge, gold CTA button
- Pro card is visually elevated (scale 1.04 on desktop) to draw the eye

### Pricing Section CTAs

| Tier | CTA Label |
|---|---|
| Free | Start Free → |
| Pro | Upgrade to Pro → |
| Elite | Go Elite → |

---

## 11. Feature Hierarchy

### Tier 1 Features (S05 — 6-Card Grid, Above-the-Fold Priority)

These are the features that close the purchase decision. They directly counter competitor weaknesses.

| Priority | Feature | Icon | Description Copy |
|---|---|---|---|
| 1 | **Visual Strategy Builder** | `GitBranch` | Drag-and-drop node graph. Connect market data, indicators, ML models, and logic gates — no code. Export as production-ready bot in one click. |
| 2 | **VectorBT Backtesting Engine** | `Activity` | Simulate strategies across years of high-fidelity tick data. Metrics: Sharpe, Sortino, Calmar, max drawdown, profit factor, expectancy. |
| 3 | **Paper Trading Mode** | `Bot` | Forward-test in live market conditions with simulated capital. Real exchange WebSocket data, real latency, zero financial risk. |
| 4 | **ML / XGBoost Node** | `Brain` | Insert a machine learning model directly into your strategy DAG. Train on your backtest data. No Python required. |
| 5 | **Institutional Risk Controls** | `Shield` | Kill switch, drawdown monitor, daily loss limits, position-size caps. Automatic safety gate on position drift. |
| 6 | **Strategy Marketplace** | `Store` | Discover, clone, and rate community strategies. Publish your own. Live performance metrics on every listing. |

### Tier 2 Features (Accessible via "See All Features" expansion or dedicated Features page)

| Feature | Context |
|---|---|
| Multi-Exchange Support (50+ via CCXT.pro) | Supports Binance, Bybit, OKX, Kraken, Coinbase, and 45+ more |
| Real-Time WebSocket Streams | Ticker, orderbook, candles, P&L, fills — all live |
| Signal Trace Visualisation | Debug exactly which node fired which signal |
| Monte Carlo Simulation | Stress-test strategy under hundreds of randomised scenarios |
| Walk Forward Analysis | Out-of-sample validation to prevent overfitting |
| DAG Compiler & Cycle Detection | Production-grade 10-step DAG validation before deploy |
| AES-256 Credential Vault | Exchange API keys never exposed in shared memory |
| Event Log & Audit Trail | Full timestamped audit trail of every strategy event |
| Sentry Monitoring + Prometheus | Institutional observability stack |
| Desktop App (Tauri) | Native Windows/macOS/Linux application available |
| Supabase RLS Data Isolation | Your data is row-level isolated from all other users |

### Feature Section Layout

**Primary Grid:** 3 columns × 2 rows. Each card is a `LandingFeatureCard` variant.

**Card Anatomy:**
1. Icon (in rounded square, accent-tinted background)
2. Feature name (weight 900)
3. Description (2–3 lines, monospace, muted)
4. Optional: "Pro" or "Elite" badge in top-right corner for gated features

**Grid End CTA:** "See Full Feature Comparison →" (ghost, links to pricing section)

---

## 12. FAQ Architecture

### FAQ Philosophy

FAQs are not a help resource — they are an **objection-handling system**. Each question directly corresponds to a measurable conversion blocker.

### FAQ Structure

**2 Categories. 4 Questions Each.**

#### Category 1: Platform & Capabilities

| # | Question | Objection Being Handled |
|---|---|---|
| 1 | Do I need coding experience to use VyomQuant? | "I'm not a developer" anxiety |
| 2 | What is the difference between Paper Trading and Live Trading? | Confusion about commitment level |
| 3 | Which exchanges are supported? | "My exchange isn't supported" |
| 4 | Can I use machine learning models without knowing Python? | "ML is for experts only" |

#### Category 2: Billing & Account

| # | Question | Objection Being Handled |
|---|---|---|
| 5 | Is my exchange API key information secure? | Security anxiety (highest-stakes objection) |
| 6 | Can I cancel or change my subscription tier at any time? | Commitment phobia |
| 7 | What happens to my strategies if I downgrade to Free? | Fear of losing work |
| 8 | Is there a free trial for Pro or Elite features? | "I want to try before I pay" |

### FAQ Visual Presentation

**Layout:** Centred max-width container (900px). Card wrapper with subtle border.

**Item Anatomy:**
- Question row: `[Question Text] [+/− Icon]` — full-width clickable
- Answer: Smooth height animation on open (CSS max-height transition)
- Active item: Accent colour underline on question text
- Answer text: Monospace, muted, 1.6 line-height

**Category Headers:** Each category has a small monospace label above its group of questions. (e.g., "// PLATFORM" and "// BILLING")

**Default State:** First question in each category open by default (pre-loads content, reduces perceived friction).

---

## 13. Mobile Layout System

### Breakpoints

| Breakpoint | Range | Layout Mode |
|---|---|---|
| Mobile S | 320–480px | Single column, stacked |
| Mobile L | 481–767px | Single column, wider padding |
| Tablet | 768–1023px | 2-column grid |
| Desktop | 1024–1440px | 3-column grid (standard) |
| Wide | 1441px+ | Constrained max-width centred |

### Mobile-Specific Adaptations Per Section

**S01 — Nav:**
- Desktop: Full horizontal nav links
- Mobile: Logo left + hamburger right. Hamburger slides in a full-screen overlay menu.
- Sticky CTA on mobile: Floating "Start Free" pill button, bottom-right corner (like a chat widget)

**S02 — Hero:**
- H1 font size reduces via `clamp()`. No manual breakpoints needed.
- Dual CTA buttons stack vertically (full width)
- Hero visual panel (terminal preview) moves below the copy block
- Trust badge row stacks into 3 lines (one badge per line)

**S04 — Product Preview:**
- Tab bar scrolls horizontally (overflow-x scroll)
- Preview panel is full-width, aspect-ratio preserved
- Floating metric chips hidden on mobile (too cluttered)

**S05 — Feature Grid:**
- Desktop: 3 columns
- Tablet: 2 columns
- Mobile: 1 column (full-width cards)

**S06 — How It Works:**
- Desktop: Horizontal 3-step stepper
- Mobile: Vertical stepper with connecting vertical line

**S07 — Marketplace Teaser:**
- Desktop: Left copy + Right 2×2 card grid
- Mobile: Copy block on top, cards carousel below (horizontal swipe, 1.2 cards visible to signal scrollability)

**S08 — Pricing:**
- Desktop: 3 columns. Pro card slightly elevated.
- Mobile: Vertical stack. Pro card at top (reordering for conversion priority on mobile).
- Annual toggle remains full-width

**S10 — Testimonials:**
- Desktop: 3-column grid
- Mobile: Horizontal swipe carousel (touch-native)

**S11 — FAQ:**
- No layout change. Accordion is inherently mobile-friendly.

**S12 — Final CTA Band:**
- Mobile: Full-width CTA button, secondary CTA as text link below

### Touch-Specific Patterns

- All interactive elements: minimum touch target 44×44px
- Hover effects disabled on touch (use `:hover` with `@media (hover: hover)` guard)
- Carousel snap points on all carousels
- Tab switching panels: swipeable on mobile
- No tooltips on mobile (inaccessible on touch)

### Performance on Mobile

- Hero visual panel: On mobile, use a static screenshot instead of animated preview
- Particle/grid animations in S12: Disabled on mobile (`prefers-reduced-motion` and low-DPR check)
- Count-up animations in S03: Reduced duration on mobile (0.8s vs 1.2s)
- All images: `loading="lazy"` on anything below the fold

---

## 14. Design Tokens & Visual Language

*This section formalises the design system tokens that the implementation must respect, derived from the existing `algo22-terminal` design system.*

### Colour Palette

| Token | Role | Hex Equivalent |
|---|---|---|
| `--bg-deep` | Page background | `#080A0E` |
| `--bg-surface` | Card/panel surface | `#0F1117` |
| `--bg-elevated` | Elevated card surface | `#151821` |
| `--border-default` | Default border | `#1E2530` |
| `--text-primary` | Primary text | `#F8FAFC` |
| `--text-secondary` | Body copy | `#94A3B8` |
| `--text-muted` | Labels, captions | `#64748B` |
| `--accent-cyan` | Primary accent / CTA | `#00D4FF` |
| `--accent-profit` | Success / positive metric | `#10B981` |
| `--accent-gold` | Elite tier / premium | `#F59E0B` |
| `--accent-loss` | Warning / danger | `#EF4444` |

### Typography Scale

| Level | Size | Weight | Font |
|---|---|---|---|
| Display / H1 | `clamp(2.6rem, 6.5vw, 5.2rem)` | 900 | Inter |
| H2 Section | `2rem` | 900 | Inter |
| H3 Card Title | `1.125rem` | 700 | Inter |
| Body | `0.875rem` | 400 | Inter |
| Label / Monospace | `0.75rem` | 700 | JetBrains Mono / monospace |
| Caption | `0.625rem` | 400 | JetBrains Mono |

### Spacing Scale

8px base unit. All spacing is multiples of 8: `8 · 16 · 24 · 32 · 48 · 64 · 96 · 128px`.

### Radius Scale

| Token | Value | Usage |
|---|---|---|
| `--radius-sm` | `8px` | Inputs, small tags |
| `--radius-md` | `12px` | Buttons |
| `--radius-lg` | `16px` | Cards |
| `--radius-xl` | `20px` | Pricing cards |
| `--radius-2xl` | `24px` | Modal panels |

---

## 15. Navigation System

### Sticky Header Behaviour

**Scroll States:**

| Scroll Position | Nav Appearance |
|---|---|
| 0–80px | Transparent, no border, text white |
| 80px+ | `backdrop-filter: blur(16px)` · `background: rgba(8,10,14,0.85)` · bottom border appears |
| Section in viewport | Corresponding nav item highlighted with accent underline |

### Scroll-Spy Implementation Pattern

As the user scrolls, the active section is detected and the corresponding nav item receives an `active` class. This is implemented with `IntersectionObserver` (not scroll events, for performance).

### Progress Indicator

Thin accent-colour progress bar at the very top of the viewport (above the nav). Width is proportional to page scroll position. Communicates page length to the user and adds a premium "reading experience" feel.

### Internal Anchor Links

All nav items link to section anchors. Smooth scroll behaviour with offset to account for sticky nav height (`scroll-margin-top: 80px` on each target section).

---

## 16. Micro-Animation Strategy

### Animation Principles

1. **Purpose over decoration.** Every animation must serve a UX function: communicate state, guide attention, or confirm an action.
2. **Performance ceiling.** No animation that triggers layout reflow. Use `transform` and `opacity` only.
3. **Accessibility.** All animations respect `prefers-reduced-motion: reduce`.
4. **Duration budget.** No animation > 400ms for UI feedback. Page-entry animations max 1200ms.

### Animation Inventory

| Animation | Trigger | Duration | Technique |
|---|---|---|---|
| Hero gradient glow pulse | Continuous | 4s loop | CSS `@keyframes` |
| Primary CTA breathing glow | Continuous | 3s loop | CSS `@keyframes` |
| Feature cards — lift on hover | Mouse enter | 300ms | `transform: translateY(-4px)` + shadow |
| Pricing cards — lift on hover | Mouse enter | 300ms | `transform: translateY(-6px)` + glow |
| S03 stat counters — count-up | On scroll into viewport | 1200ms | `IntersectionObserver` + JS counter |
| How-it-works connecting line | On scroll into viewport | 800ms | CSS `stroke-dashoffset` or `width` animation |
| Product preview tab switch | Tab click | 200ms | `opacity` fade |
| FAQ accordion open/close | Click | 250ms | `max-height` CSS transition |
| Nav background blur | 80px scroll | 150ms | `transition: background` |
| Eyebrow pill badge shimmer | Continuous | 2s loop | CSS gradient sweep |
| Sticky CTA (mobile) — appear | After 2s on page | 400ms | `transform: translateY(0)` slide up |

---

## 17. SEO & Metadata Specification

### `<title>` Tag

```
VyomQuant — No-Code Algorithmic Trading Platform | Build, Backtest & Deploy Bots
```

### Meta Description

```
VyomQuant is the institutional-grade no-code platform for algorithmic traders.
Visual strategy builder, VectorBT backtesting, ML model nodes, paper trading,
and live bot deployment — without writing code. Start free.
```

### Open Graph Tags

```html
<meta property="og:title"       content="VyomQuant — Build Algorithmic Trading Strategies Without Code" />
<meta property="og:description" content="Visual DAG builder, ML backtesting, paper trading & live execution. Join 12,400+ traders." />
<meta property="og:image"       content="https://vyomquant.linkpc.net/og-image.png" />
<meta property="og:url"         content="https://vyomquant.linkpc.net" />
<meta property="og:type"        content="website" />
```

### H1 Tag (exactly one per page)

```
Build, Backtest, and Deploy Crypto Trading Strategies — Without Writing Code.
```

### Structured Data (JSON-LD)

Apply `SoftwareApplication` schema:

```json
{
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  "name": "VyomQuant",
  "applicationCategory": "FinanceApplication",
  "operatingSystem": "Web, Windows, macOS, Linux",
  "offers": [
    { "@type": "Offer", "name": "Free", "price": "0", "priceCurrency": "USD" },
    { "@type": "Offer", "name": "Pro", "price": "12", "priceCurrency": "USD" },
    { "@type": "Offer", "name": "Elite", "price": "24", "priceCurrency": "USD" }
  ]
}
```

### Target Keywords

| Intent | Keyword |
|---|---|
| Primary | algorithmic trading platform no-code |
| Primary | build trading bot without coding |
| Secondary | backtest trading strategy free |
| Secondary | crypto trading bot platform |
| Long-tail | visual strategy builder algorithmic trading |
| Long-tail | paper trading bot deployment |
| Long-tail | XGBoost trading strategy ML |

---

## 18. Open Questions for Stakeholder Review

The following items require explicit decisions before implementation begins.

> [!IMPORTANT]
> **Q1 — Live Execution Marketing:**  
> The platform currently defaults to `AERORA_MODE=paper`. Should the landing page hero explicitly say "paper trading" is the default, or should live execution be marketed as the headline feature (with paper mentioned as the safe entry point)?  
> _Recommendation: Lead with "Deploy bots" (implies live capability), disclose paper default in hero trust badges._

> [!IMPORTANT]
> **Q2 — Annual Pricing Toggle:**  
> Are annual pricing plans available? What is the discount rate (20% assumed here)?  
> _Required before implementing the pricing section toggle._

> [!WARNING]
> **Q3 — Strategy Marketplace Status:**  
> The marketplace is currently mock-only (no backend). Should it be shown on the landing page as a live feature, or labeled "Coming Soon"?  
> _Recommendation: Show as a preview with "Early Access" badge. Do not claim live functionality until `feature/strategy-marketplace-v1` ships._

> [!IMPORTANT]
> **Q4 — Demo Booking Integration:**  
> The current demo modal submits to `alert()`. What is the real destination — Calendly embed, Google Form, or email webhook?

> [!NOTE]
> **Q5 — Testimonials:**  
> Are any real user testimonials available, or should the initial launch use illustrative user personas with a "Beta Tester" attribution?

> [!NOTE]
> **Q6 — OG Image:**  
> A 1200×630px Open Graph image is required for link previews. This should be a rendered screenshot of the Strategy Builder terminal.

> [!WARNING]
> **Q7 — Regulatory Jurisdiction:**  
> Is a specific risk disclaimer required for Indian regulatory compliance (SEBI)? If targeting Indian retail traders, additional disclaimers may be required in the footer and pricing sections.

---

*Document prepared for VyomQuant design review.*  
*UX Architecture only — no code has been modified or generated.*  
*For implementation, pair this document with `PROJECT_ARCHITECTURE.md` and `DOMAIN_DEPLOYMENT_PLAN.md`.*
