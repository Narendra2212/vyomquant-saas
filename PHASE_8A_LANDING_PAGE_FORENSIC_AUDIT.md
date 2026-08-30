# PHASE 8A — VYOMQUANT LANDING PAGE FORENSIC & PRODUCT AUDIT

**Classification:** PRODUCTION AUDIT — READ-ONLY
**Report Date:** 2026-08-30
**Auditor:** Principal Platform & Security Audit Engineer (Antigravity)
**Audit Protocol:** Zero source modifications — inspection only
**Phase 8A Verdict:** See Section 20

---

## 1. EXECUTIVE SUMMARY

Phase 8A performed a complete forensic, product-truth, security, UX, accessibility, performance, and SEO audit of the VYOMQUANT public landing page surface.

**Verdict: CONDITIONAL PASS**

No P0 or P1 security or authorization vulnerabilities were found. The authentication boundary between the public landing page and the authenticated application is structurally sound. Several P2 and P3 product-truth, UX, and structural findings require remediation in Phase 8B.

### Summary Matrix

| Category | P0 | P1 | P2 | P3 | INFO |
|:---|:---:|:---:|:---:|:---:|:---:|
| Security | 0 | 0 | 0 | 0 | 1 |
| Authorization Boundary | 0 | 0 | 1 | 0 | 0 |
| Product Truth | 0 | 0 | 0 | 4 | 2 |
| UX / Conversion | 0 | 0 | 0 | 1 | 1 |
| SEO / Technical | 0 | 0 | 0 | 1 | 1 |
| Legal / Trust | 0 | 0 | 0 | 1 | 0 |
| **Total** | **0** | **0** | **1** | **7** | **5** |

---

## 2. CURRENT REPOSITORY BASELINE

| Field | Value |
|:---|:---|
| Git HEAD | `f0e4fc6` (feat: complete trading-lifecycle-integration spec) |
| Branch | `main` (up to date with `origin/main`) |
| Working Tree | Clean (1 untracked `.kiro/specs/` directory — non-source) |
| Phase 7D Status | CONDITIONAL PASS — Auth gate authorized for production |
| Frontend Build | `✓ built in 2m 34s` (exit code 0, completed Phase 7D) |
| Backend Regression | 33/33 PASS (Phase 7D) |

**Confirmed Baseline:** The repository state audited in Phase 8A is the post-Phase 7D production candidate.

---

## 3. LANDING PAGE ARCHITECTURE

### 3.1 Active Route and Entrypoint

| Route | Component | Type |
|:---|:---|:---|
| `/` | `src/components/landing/LandingPage.jsx` | **PRIMARY — Active** |
| `/` (legacy) | `src/pages/Landing.jsx` | **LEGACY — Not mounted at `/`** |

**Critical finding:** There are TWO landing page implementations in the codebase:

1. **`src/components/landing/LandingPage.jsx`** — The modern, actively mounted component. This is what visitors see. Imported in `App.jsx` line 21 as `LandingPage` and routed to `/`.

2. **`src/pages/Landing.jsx`** — A legacy implementation (631 lines). It is **not mounted at any route** in the current `App.jsx`. Contains older design, testimonials, and different content.

The legacy `Landing.jsx` is orphaned code. All audit findings below refer to the active `LandingPage.jsx` and its sub-components unless otherwise noted.

### 3.2 Component Architecture of Active Landing Page

```
/ (route)
└── <LandingPage /> [src/components/landing/LandingPage.jsx]
    ├── <Navbar />              [Navbar.jsx]          — Fixed navbar with Sign In, Launch App CTAs
    ├── <Hero />                [Hero.jsx]            — Headline, 3 platform cards, DAG mockup
    ├── <TrustSection />        [TrustSection.jsx]    — 6 feature cards
    ├── <SecuritySection />     [SecuritySection.jsx] — 6 security claim cards
    ├── <ScreenshotsSection />  [ScreenshotsSection.jsx] — 4 placeholder IMG areas
    ├── <ModernTradingSection /> [ModernTradingSection.jsx] — 4 capability cards
    ├── <FounderSection />      [FounderSection.jsx]  — Founder bio (Narendra Tripathi / NIT AP)
    ├── <HowItWorks />          [HowItWorks.jsx]      — Steps section
    ├── <DownloadSection />     [DownloadSection.jsx] — Windows / macOS / Linux download
    ├── <Pricing />             [Pricing.jsx]         — Live API-fetched pricing, USD+INR toggle
    ├── <FAQ />                 [FAQ.jsx]             — 8 Q&A accordion items
    ├── <Waitlist />            [Waitlist.jsx]        — Waitlist form (closed beta)
    ├── <FinalCTA />            [FinalCTA.jsx]        — Final conversion CTA
    └── <Footer />              [Footer.jsx]          — Legal links, socials, risk disclaimer
```

### 3.3 Orphaned Component: `AICopilot.jsx`

`AICopilot.jsx` is explicitly marked **DORMANT / UNMOUNTED** in its file header. It is present in the directory but not imported in `LandingPage.jsx`. Contains AI Copilot claims that are not surfaced on the live landing page.

### 3.4 Assets and Fonts

- **Fonts:** IBM Plex Mono (Google Fonts, injected via `<style>` in `AppShell`). The landing page itself uses TailwindCSS classes (`font-mono`).
- **Images:** All product screenshots are **placeholder boxes** with text `Screenshot Coming Soon` — no real product screenshots exist on the page.
- **Icons:** Lucide React (tree-shaken, safe).
- **Favicon:** `/vite.svg` — generic Vite logo, not a branded VYOMQUANT favicon.
- **OG Image:** `https://vyomquant.in/og-image.png` — referenced in meta tags; existence in `dist/` not verified (external URL reference).

### 3.5 Third-Party Scripts

| Script | Source | Condition |
|:---|:---|:---|
| Sentry Browser SDK | `https://js.sentry-cdn.com/browser.min.js` | Only loaded if `VITE_SENTRY_DSN` is non-dummy |
| Google Analytics (gtag.js) | `https://www.googletagmanager.com/gtag/js` | Only loaded if `VITE_GA_TRACKING_ID` is non-dummy |
| Microsoft Clarity | `https://www.clarity.ms/tag/<id>` | Only loaded if `VITE_CLARITY_PROJECT_ID` is non-dummy |

All three are dynamically loaded via guard functions in `index.html`. No unconditional third-party scripts. Dev-mode fallbacks correctly gate to `window.__VQ_LOCAL__`.

---

## 4. ROUTE MAP

### 4.1 Public Route Inventory

| Route | Guard | Description |
|:---|:---:|:---|
| `/` | None | Landing page |
| `/download` | None | Download page |
| `/download#windows` | None | Windows download anchor |
| `/download#macos` | None | macOS download anchor |
| `/marketplace` | None | Strategy Marketplace (public, no auth) |
| `/admin/waitlist` | **None** | AdminDashboard — waitlist management UI |
| `/legal`, `/legal/privacy`, `/legal/terms`, `/legal/risk`, `/legal/refund` | None | Legal pages |
| `/signin` | GuestGuard (redirect to `/app/dashboard` if authed) | Sign In |
| `/signup` | GuestGuard (redirect to `/app/dashboard` if authed) | Sign Up |
| `/reset-password` | None | Password reset |
| `/2fa` | None | MFA setup |
| `/wizard` | None | Onboarding wizard |

### 4.2 Authenticated Route Inventory

All `/app/*` routes are protected by `AuthGuard` which checks for a session token in `sessionStorage`.

| Route | Guard |
|:---|:---|
| `/app/dashboard` | AuthGuard |
| `/app/strategies`, `/app/builder`, `/app/backtest` | AuthGuard |
| `/app/exchange`, `/app/risk`, `/app/billing` | AuthGuard |
| `/app/profile`, `/app/security-logs` | AuthGuard |
| All other `/app/*` | AuthGuard → redirect to `/app/dashboard` |

### 4.3 Sign-Up Flow

```
Landing "/" → "Start Free" / "Launch Web App" / Pricing CTA → /signup
  ↓ (GuestGuard: if already authed → /app/dashboard)
AuthPage mode="signup"
  ↓ (email/password registration)
Supabase sign_up() → Email verification required
  ↓ (user clicks email link → Supabase hash → PasswordRecoveryHandler)
/app/dashboard
  ↓ (Wizard onboarding at /wizard)
Authenticated Application
```

### 4.4 Sign-In Flow

```
Landing "/" → Navbar "Sign In" → /signin
  ↓ (GuestGuard: if already authed → /app/dashboard)
AuthPage mode="signin"
  ↓ Supabase signInWithPassword()
  ↓ JWT stored in sessionStorage
/app/dashboard
Authenticated Application
```

### 4.5 "Launch App" / "Launch Web App" CTAs

`Navbar`, `Hero`, `FinalCTA`, and `Footer` all link to `/app` (not `/signin` or `/signup`). 

`/app` → `Navigate to="/app/dashboard" replace` → hits `AuthGuard`:
- If **authenticated**: renders `AppShell` with `Dashboard`
- If **not authenticated**: `Navigate to="/signin" replace`

This is the correct flow. However, the label **"Launch App"** implies the app is immediately accessible — unauthenticated visitors clicking it will be silently bounced to `/signin`. There is no messaging explaining that sign-in is required.

### 4.6 "Book Demo" Modal

The "Book Demo" button in `Landing.jsx` (legacy, not active) shows a modal that calls `alert()` on submission — a non-functional demo request. This component is **not mounted** at any live route. No functional issue on the live page.

---

## 5. AUTHENTICATION / CTA AUDIT

### 5.1 CTA Inventory (Active Landing Page)

| Location | Label | Destination | Behavior |
|:---|:---|:---|:---|
| Navbar (desktop) | "Sign In" | `/signin` | Correct GuestGuard |
| Navbar (desktop) | "Launch App" | `/app` | Auth-bounces to `/signin` if unauthed |
| Navbar (mobile) | "Sign In" | `/signin` | Correct |
| Navbar (mobile) | "Launch Web App" | `/app` | Auth-bounces to `/signin` |
| Hero | "Web Application" card | `/app` | Auth-bounces to `/signin` |
| Hero | "Windows Desktop App" | `/download#windows` | Correct public route |
| Hero | "macOS Desktop App" | `/download#macos` | Correct public route |
| Pricing | "Start Free" | `/signup` | Correct GuestGuard |
| Pricing | "Get Started" | `/signup` | Correct GuestGuard |
| FinalCTA | "Launch Web App" | `/app` | Auth-bounces to `/signin` |
| FinalCTA | "Download Windows" | `/download#windows` | Correct |
| FinalCTA | "Download macOS" | `/download#macos` | Correct |
| Footer | "Web Application" | `/app` | Auth-bounces to `/signin` |
| Footer | "Documentation" | `/docs` | No route exists — 404 catch-all → `/` |
| Footer | company links | `#about`, `#contact`, `#careers` | Scroll-to anchors — sections do not exist on active landing |

### 5.2 Redirect Parameter Audit

No `next`, `returnTo`, `redirect`, `callback`, `destination`, `continue`, or `redirectTo` parameters were found in any CTA URL. All redirects are internal React Router `<Navigate>` components. No open redirect vectors found.

### 5.3 Token / Session Leakage via URL

`PasswordRecoveryHandler` in `App.jsx` extracts `access_token` from the URL hash (`#access_token=...`) on Supabase OAuth/recovery flows and strips it from the URL using `window.history.replaceState()` (line 151). This is the correct Supabase PKCE/implicit flow handling. Token is stored in `sessionStorage` (not `localStorage`), which is tab-scoped and does not persist across sessions.

No token, JWT, or session credential is injected into URL query strings as part of the CTA flow.

### 5.4 Session-Aware Guard Behavior

| Scenario | Behavior |
|:---|:---|
| Authenticated user clicks "Sign In" | `GuestGuard` → redirects to `/app/dashboard` ✅ |
| Authenticated user clicks "Sign Up" | `GuestGuard` → redirects to `/app/dashboard` ✅ |
| Authenticated user clicks "Launch App" | `AuthGuard` → renders dashboard ✅ |
| Unauthenticated user clicks "Launch App" | `AuthGuard` → redirects to `/signin` ✅ |
| `SIGNED_OUT` event via Supabase | Removes token from `sessionStorage`, no redirect ⚠️ (landing page only) |
| Expired session | Next `AuthGuard` check bounces to `/signin` ✅ |

---

## 6. PRODUCT TRUTH AUDIT

Each material product claim is evaluated against actual implementation evidence.

| # | Claim | Location | Classification | Evidence |
|:--|:---|:---|:---:|:---|
| 1 | "Build, Backtest and Deploy Quantitative Trading Strategies Without Writing Code" | Hero h1 | **SUPPORTED** | Strategy Builder (DAG) + Backtester pages exist and are functional |
| 2 | "Institutional-grade algorithmic trading infrastructure" | Hero subheadline | **MARKETING CLAIM REQUIRING QUALIFICATION** | Backend is built with FastAPI/Supabase; "institutional-grade" is aspirational, not independently certified |
| 3 | "visual strategy design, vectorized backtesting, paper trading, AI assistance, and live execution" | Hero subheadline | **PARTIALLY SUPPORTED** | DAG builder ✅, VectorBT backtesting ✅, paper trading ✅, live execution ✅ under safety freeze. "AI assistance" is **UNSUPPORTED** — AICopilot.jsx is explicitly DORMANT/UNMOUNTED |
| 4 | "Closed Beta — Early Access Available" | Hero badge | **SUPPORTED** | Waitlist section present; consistent with platform state |
| 5 | "Web Application / Windows Desktop App / macOS Desktop App" | Hero checkmarks | **PARTIALLY SUPPORTED** | Web app ✅, download page present ✅; actual desktop installer binaries not verified in this audit scope |
| 6 | "LIVE EXECUTION" badge in hero DAG mockup | Hero UI mockup | **STALE / MISLEADING** | The badge shows in a static HTML mockup — this is a visual illustration, not live data. The `SafetyMonitor.assert_safe_mode()` is active and the execution engine is under a FREEZE flag. "LIVE EXECUTION" label on a static mockup is misleading. |
| 7 | "Win Rate: 67.4%" and "Sharpe: 2.1" shown in hero DAG | Hero UI mockup | **UNSUPPORTED** | These are hardcoded illustrative values in the static mockup, not derived from real user backtest data. No disclaimer identifies them as illustrative. |
| 8 | "VectorBT-Powered Backtesting — Run millions of backtest iterations in seconds" | TrustSection | **PARTIALLY SUPPORTED** | VectorBT is integrated ✅. "Millions of iterations in seconds" depends on hardware; unqualified absolute performance claim. |
| 9 | "Multi-Exchange Connectivity — Deploy to over 50+ global cryptocurrency exchanges" | TrustSection | **PARTIALLY SUPPORTED** | CCXT.pro integration exists ✅; "50+" supported exchanges is accurate for CCXT.pro library, but only a subset may be production-tested/supported. "50+" in copy is CCXT's total, not VyomQuant-verified subset. |
| 10 | "Execute orders with sub-millisecond latency" | ModernTradingSection | **UNSUPPORTED** | No latency benchmarks in repository. CCXT.pro over HTTPS/WebSocket to exchange APIs does not guarantee sub-millisecond latency. This is a strong, unsubstantiated performance claim. |
| 11 | "Secure Local Execution — With our desktop applications, your strategy logic remains on your machine. We never see your proprietary alpha." | ModernTradingSection | **AMBIGUOUS** | Desktop app architecture is not fully verifiable in this scope. Web app execution runs server-side. Claim needs qualification: applies to desktop app only, not the web app. |
| 12 | "Extensible Infrastructure — Seamlessly integrate external alternative data feeds, custom Python models, and institutional FIX connections." | ModernTradingSection | **PARTIALLY SUPPORTED** | Python custom models: partially ✅ (XGBoost node). FIX protocol connections: **not verified** in backend. Alternative data feeds: not verified. |
| 13 | "Advanced Order Types — Natively support TWAP, VWAP, Iceberg, and dynamic trailing stops" | ModernTradingSection | **UNVERIFIED** | CCXT supports these where exchanges do; VyomQuant-layer TWAP/VWAP/Iceberg implementation not verified in backend router audit scope. |
| 14 | "AES-256 Encryption — All sensitive data and API keys are encrypted at rest" | SecuritySection | **SUPPORTED** | `SecurityVault` with AES-256 GCM confirmed in codebase (Phase 7A audit) |
| 15 | "Secure API Key Storage — Exchange credentials never stored in plaintext, injected at runtime" | SecuritySection | **SUPPORTED** | Verified in Phase 7A/7B: service-role key vault, keys encrypted at rest |
| 16 | "Role-Based Access Control — Granular RBAC for users and organizations" | SecuritySection | **PARTIALLY SUPPORTED** | RBAC exists at admin/operator/user level ✅. "Organization sub-accounts" RBAC: not verified. |
| 17 | "Audit Logging — Comprehensive, immutable audit trails for all critical actions including logins, strategy modifications, and order routing" | SecuritySection | **PARTIALLY SUPPORTED** | Security logs page exists ✅; "immutable" is a strong claim not independently verified against DB constraints. |
| 18 | "Password hashing using Argon2id" | SecuritySection | **AMBIGUOUS** | Password hashing is fully delegated to Supabase Auth (not local). Supabase uses bcrypt by default. Argon2id claim may be inaccurate — requires Supabase version verification. |
| 19 | "Execution engines run in isolated, protected VPC environments" | SecuritySection | **AMBIGUOUS / UNVERIFIED** | Infrastructure architecture claim not verifiable from repository. May be aspirational for production deployment. |
| 20 | "50+ exchanges via CCXT.pro integration, including Binance, Bybit, OKX, Kraken, and Coinbase" | FAQ | **PARTIALLY SUPPORTED** | CCXT.pro supports 50+. VyomQuant testing/support scope not documented. |
| 21 | "The XGBoost node accepts parameter configuration through the visual interface. Model training runs on managed infrastructure. No local Python environment required." | FAQ | **PARTIALLY SUPPORTED** | XGBoost integration exists ✅. "Managed infrastructure" training not fully verified. |
| 22 | "Row-level security isolation ensures your credentials are logically separated" | FAQ | **SUPPORTED** | RLS via Supabase confirmed architecture (Phase 7A) |
| 23 | Testimonials: "Sarah K. — Quantitative Trader", "David L. — Asset Manager", "Michael R. — Crypto Scalper" | `pages/Landing.jsx` (legacy — NOT mounted) | **UNSUPPORTED / NOT LIVE** | These are named testimonials in the orphaned `Landing.jsx` only. Not visible on live page. However, the quote by Michael R. claims "Sub-millisecond execution and reliable paper trading. My forward testing matches live results perfectly." — an unsubstantiated claim if these testimonials were ever surfaced. |
| 24 | Annual billing "Save 20%" toggle | Pricing section | **PARTIALLY SUPPORTED** | 20% discount math is hardcoded in UI. Actual billing enforcement (Razorpay/Stripe annual plans) not verified in this audit. |
| 25 | Pricing plans loaded live from API | Pricing.jsx | **SUPPORTED** | `api.billing.getPlans()` is called on mount; pricing is server-driven, not hardcoded in the landing page |

---

## 7. SECURITY AUDIT

### 7.1 XSS Vector Scan

No `dangerouslySetInnerHTML`, `eval()`, `innerHTML`, `document.write`, `javascript:` href, or `window.open` was found in any active landing component. **CLEAN.**

### 7.2 Credential / Secret Exposure in Source

- `index.html`: Environment variable placeholders `%VITE_SENTRY_DSN%`, `%VITE_GA_TRACKING_ID%`, `%VITE_CLARITY_PROJECT_ID%` are injected at build time. In development, these remain as literal placeholder strings which the guard functions correctly detect and no-op. **CLEAN.**
- No hardcoded Supabase keys, JWT secrets, API keys, or credentials found in any landing component.
- `supabase.js`: Only `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` (public anon key — safe to expose in browser) are loaded via `import.meta.env`.

### 7.3 Production Bundle Secret Scan

A text search of `algo22-terminal/dist/assets/LandingPage-ePOD8spG.js` for `VITE_SUPABASE`, `VITE_SENTRY`, `supabaseKey`, `service_role`, and JWT patterns (`eyJhb`) returned no matches. **CLEAN.**

The `index-Bxq81Ijm.js` bundle was scanned for `access_token`, `refresh_token`, `service_role`, `SUPABASE_SERVICE_ROLE`, `password`, `apikey`, `API_KEY`. The scan returned matches for these strings only in the context of request processing logic and Supabase auth client API call parameters — no hardcoded token values. **CLEAN.**

### 7.4 Console Logging on Landing Page

The only `console.error` call in active landing components is in `Pricing.jsx` line 18: `console.error('Failed to load plans:', err)`. This logs the error object on plan API failure, which may include internal error details. **INFO — low risk, non-credential.**

### 7.5 Iframe Usage

No `<iframe>` elements found in any active landing component. **CLEAN.**

### 7.6 External Resource Loading

External resources loaded from landing:
- Google Fonts: CSS/font files (standard, non-sensitive)
- Lucide React icons: bundled, not external
- Third-party analytics: guarded (see Section 3.5)

**CLEAN.**

### 7.7 Public Page Cannot Access Authenticated Data

Verified: All API calls in landing components are:
1. `api.billing.getPlans()` — a public pricing endpoint, no auth required
2. Waitlist submit (Supabase direct, anon key) — public by design

No landing component calls any authenticated `/api/auth/...`, `/api/admin/...`, `/api/orders/...`, or similar endpoints. **CLEAN.**

---

## 8. AUTHORIZATION BOUNDARY AUDIT

### 8.1 Landing / App / Admin Separation

```
Public Landing (/)
        ↓ (no token in sessionStorage)
AuthGuard → /signin
        ↓ (valid JWT in sessionStorage)
Authenticated App (/app/*)
        ↓ (app_metadata.role == "admin" required server-side)
Admin Functionality (/api/admin/*)
```

The boundaries are enforced at the backend (`get_admin_user()` in `dependencies.py`) and at the frontend router level (`AuthGuard`). **Structurally sound.**

### 8.2 CRITICAL: Admin Waitlist Dashboard at Unprotected Route

**Finding:** `/admin/waitlist` renders `AdminDashboard.jsx` without any `AuthGuard`, `GuestGuard`, or role check at the router level:

```jsx
// App.jsx line 271
<Route path="/admin/waitlist" element={<Suspense ...><AdminDashboard /></Suspense>} />
```

`AdminDashboard.jsx` calls `waitlistAdminApi.getAll()` which queries the `waitlist` table directly from the Supabase client using the anon key. If the Supabase RLS policy on the `waitlist` table does not restrict reads to admin roles, **any unauthenticated visitor can navigate to `/admin/waitlist` and view all waitlist entries** (names, emails, Telegram handles, experience levels).

This is classified **P2** (not P0) because:
- The actual data exposure depends on the Supabase RLS configuration which cannot be directly verified here without database access
- `waitlistAdminApi` uses the anon key (not a service role key)
- If RLS is correctly configured on the `waitlist` table, the data read will be empty or denied

**However:** The route is publicly navigable and the frontend renders no authentication gate. The security relies entirely on Supabase RLS being correctly enforced — which is an infrastructure-level dependency outside the frontend code.

**Evidence:** `App.jsx` line 271; `src/lib/waitlistApi.js` — `waitlistAdminApi.getAll()` uses `supabase.from('waitlist').select(...)` with the anon client.

**Reproduction:** Navigate to `http://localhost:1420/admin/waitlist` without any authentication.

### 8.3 URL / LocalStorage Manipulation

- Manually setting `sessionStorage.token` to an arbitrary value: `AuthGuard` passes (token presence only, no format validation at route level). The backend verifies the JWT cryptographically on every request — unauthenticated API calls fail with 401.
- Navigating directly to `/app/dashboard` without a token: `AuthGuard` redirects to `/signin`. **Correct.**
- Clearing `sessionStorage.token` while in-app: `storage` event listener in `AppShell` detects this and navigates to `/`. **Correct.**

---

## 9. UX / CONVERSION AUDIT

### 9.1 First-Time Trader Evaluation

| Question | Answer from Landing Page |
|:---|:---|
| What is VyomQuant? | A no-code platform to build, backtest, and deploy algorithmic crypto trading strategies |
| Who is it for? | Systematic traders and quants — messaging is moderately clear, but leans heavily technical |
| What problem does it solve? | Removing coding barriers from algo trading — clear |
| What can the user actually do? | Build DAG strategies, run backtests, paper trade, live trade — clear in feature section |
| Why trust it? | Security section, founder section, risk disclaimer — moderate |
| What differentiates it? | Visual DAG + VectorBT + no-code — differentiators are present but not ranked against competitors |
| What should the visitor do next? | Multiple competing CTAs: "Launch App", "Download", "Join Waitlist" — **three distinct conversion paths with no priority hierarchy** |

### 9.2 Hero CTA Ambiguity

The hero presents three equally-weighted platform cards (Web / Windows / macOS). For a conversion-focused landing page, this creates **choice paralysis**. There is no primary CTA button at the hero level (the cards serve as CTAs but don't have clear visual hierarchy establishing one as the dominant conversion action).

The legacy `Landing.jsx` (orphaned) had a clear "Start Free" primary button and a secondary "Book Demo" — superior CTA hierarchy.

### 9.3 Waitlist vs. Immediate Signup Conflict

The page simultaneously offers:
- Direct signup via `/signup` (Pricing CTAs)
- A waitlist form (`Waitlist` section — "closed beta, apply and be reviewed in 48h")

These two conversion paths contradict each other. A visitor cannot know whether they can sign up immediately or need to wait for approval.

### 9.4 Screenshots Section

All four screenshot areas show **"Screenshot Coming Soon"** placeholder boxes. This is a significant trust deficit for a product landing page — visitors cannot see what they are signing up for.

### 9.5 CTA Label Consistency Issue

The hero "Launch Web App" card links to `/app`, which bounces unauthenticated users to `/signin`. The label "Launch App" implies instant access. First-time visitors will experience a confusing redirect to a login form. The conventional CTA for unauthenticated users should be "Start Free" or "Get Started" → `/signup`.

### 9.6 Footer Dead Links

| Footer Link | Destination | Status |
|:---|:---|:---|
| "Documentation" | `/docs` | No route — redirects to `/` (global catch-all) |
| "Strategy Marketplace" | `#marketplace` | Section anchor not present on landing page |
| "About Us", "Careers", "Contact Us" | `#about`, `#contact` | Scroll anchors; no corresponding sections in active landing page |

---

## 10. RESPONSIVE / MOBILE AUDIT

### 10.1 Responsive Design Foundation

The active landing page uses TailwindCSS with responsive prefixes (`sm:`, `md:`, `lg:`). The structure uses `section-container` / `section-inner` utility classes.

### 10.2 Mobile Navbar

`Navbar.jsx` has a mobile hamburger menu (`mobileOpen` state, `<Menu>` / `<X>` icons) that renders a full-screen overlay menu on mobile. Mobile CTAs include "Sign In" and "Launch Web App". This is structurally sound.

`aria-label="Toggle Navigation Menu"` and `aria-expanded={mobileOpen}` are present. **Accessible.**

### 10.3 Hero Grid

Hero platform cards use `grid-cols-1 sm:grid-cols-3` — stacks correctly on mobile.

### 10.4 `DesktopOnlyOverlay`

The authenticated `AppShell` is wrapped in `<DesktopOnlyOverlay>`. This overlay blocks the authenticated application on small screens, requiring desktop viewport. The landing page itself has **no such restriction** — it is fully accessible on mobile.

However, when a mobile user clicks "Launch Web App" → bounces to `/signin` → signs in → enters the app → hits `DesktopOnlyOverlay`. This creates a confusing experience: the landing page accepts mobile visitors but the app rejects them.

### 10.5 DAG Hero Mockup

The hero DAG mockup uses `min-h-[280px] sm:min-h-[340px]` and `grid-cols-1 sm:grid-cols-3`. The horizontal connector arrows between DAG nodes have `hidden sm:flex` — they are hidden on mobile. This is acceptable (simplified mobile layout).

---

## 11. ACCESSIBILITY AUDIT

### 11.1 Semantic HTML

- `<section>` tags are used throughout with `aria-label` attributes ✅
- `<nav>` has `role="navigation"` and `aria-label="Main Navigation"` ✅
- `<footer>` has `role="contentinfo"` and `aria-label="Footer"` ✅
- `<h1>` appears once (Hero headline) ✅
- `<h2>` used for section headings ✅
- `<h3>` used for card headings ✅

### 11.2 Focus States

Active landing components use `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan` on interactive elements. This provides visible focus indicators for keyboard users. ✅

### 11.3 Button vs. Link Semantics

**Finding:** Multiple footer "links" (Terms, Privacy, Risk, Refund in legacy `Landing.jsx`) and navigation scroll actions use `<button>` elements with `onClick={() => navigate(...)}` instead of `<Link>` or `<a>` elements. Buttons are not crawlable by search engines and do not support middle-click or browser link behaviors.

The active `Footer.jsx` correctly uses `<Link to="/legal/privacy">` etc. ✅

Navbar scroll buttons (`<button onClick={() => scrollToSection('#features')}>`) are technically acceptable for same-page scroll, but these sections have `id` attributes that could be used as anchor links instead, which would be more accessible.

### 11.4 Alt Text and SVG Icons

- `WindowsIcon`, `AppleIcon`, `GithubIcon`, `LinkedinIcon`, `TwitterIcon` SVGs have `aria-hidden="true"` ✅
- `<Link aria-label="Download Windows Desktop App">` ✅
- `<Link aria-label="VyomQuant Home">` ✅
- No `<img>` elements with missing `alt` on active landing page (screenshots are CSS `<div>` placeholders) ✅

### 11.5 FAQ Accordion

`FAQ.jsx` uses a custom `<Accordion>` component. The landing page `FAQ` items (formerly in `Landing.jsx`) used `<button>` with expand state — accessible. The active `Accordion` component should be verified in Phase 8B for `aria-expanded`, `aria-controls`, and `id` pairing.

### 11.6 Color Contrast

The landing page uses dark theme (`#0a0e17` background) with `text-text-primary`, `text-text-secondary`, `text-text-muted`. Actual contrast ratios cannot be computed without rendering — visual verification recommended in Phase 8B. The color palette design follows WCAG-friendly dark theme conventions.

---

## 12. PERFORMANCE AUDIT

### 12.1 Production Bundle Analysis

| Asset | Minified | gzip | Risk |
|:---|---:|---:|:---|
| `vendor-recharts-Rt13QZbF.js` | 541.6 kB | 166 kB | Large — recharts loaded for all pages |
| `index-Bxq81Ijm.js` | 412.5 kB | 123 kB | Large — main bundle |
| `LandingPage-ePOD8spG.js` | 62.7 kB | 14.4 kB | Acceptable — lazy chunk |
| `StrategyBuilder-BDFsSnpJ.js` | 164.4 kB | 50.8 kB | Lazy-loaded ✅ |

**Landing page chunk size (62.7 kB)** is reasonable. The build uses `React.lazy()` for the landing page, so it is not included in the initial bundle.

### 12.2 Lazy Loading

All page components including `LandingPage` are lazy-loaded via `React.lazy()`. Route-based code splitting is implemented. ✅

### 12.3 Image Optimization

No actual images on the landing page (all screenshots are placeholder CSS). When real screenshots are added in Phase 8B, they should use WebP format with explicit `width`/`height` and `loading="lazy"`.

### 12.4 Font Loading

IBM Plex Mono is loaded via an inline `<style>@import url('https://fonts.googleapis.com/...')` inside the `AppShell` component. This is only loaded when `AppShell` renders (authenticated users). The landing page itself loads fonts via TailwindCSS `font-mono` class which falls back to system monospace. ✅ (no FOIT risk for landing page visitors)

### 12.5 Animation Performance

Landing components use CSS transitions (`transition-all duration-300`) and Tailwind animation classes. No `requestAnimationFrame` or heavy JS animation loops detected. `ctaPulse` keyframe animation is injected via a `<style>` tag inside the legacy `Landing.jsx` (orphaned — not a live concern).

### 12.6 Render-Blocking Resources

`index.html` inline scripts for analytics/Sentry are dynamically loaded (`document.createElement('script')`) and `async` — no render-blocking. ✅

---

## 13. SEO AUDIT

### 13.1 Title and Meta Description

| Element | Value | Assessment |
|:---|:---|:---|
| `<title>` | "VyomQuant — Institutional Quantitative Trading Terminal & Algorithmic SaaS" | ✅ Descriptive, includes keyword |
| `<meta name="description">` | "Institutional-grade algorithmic trading infrastructure featuring visual strategy design (DAG), VectorBT backtesting, paper trading, XGBoost ML integration, and automated exchange execution." | ✅ Rich, keyword-dense |
| `<meta name="keywords">` | Present with relevant keywords | ✅ |
| `<meta name="author">` | "VyomQuant Technologies" | ✅ |
| `<meta name="theme-color">` | `#0a0e17` | ✅ |

### 13.2 Open Graph

| Element | Value | Assessment |
|:---|:---|:---|
| `og:type` | `website` | ✅ |
| `og:url` | `https://vyomquant.in/` | ✅ |
| `og:title` | "VyomQuant — Institutional Quantitative Trading Terminal" | ✅ |
| `og:description` | Present | ✅ |
| `og:image` | `https://vyomquant.in/og-image.png` | ⚠️ Image URL is external — not verified to exist |

### 13.3 Twitter/X Meta

Present with `twitter:card`, `twitter:url`, `twitter:title`, `twitter:description`, `twitter:image`. ✅

### 13.4 JSON-LD Structured Data

```json
{
  "@type": "SoftwareApplication",
  "operatingSystem": "Web, Windows, macOS",
  "applicationCategory": "FinanceApplication",
  "offers": { "price": "0.00", "priceCurrency": "USD" },
  "description": "..."
}
```

✅ Present, correctly typed. Note: `price: 0.00` refers to the free tier, which is accurate. Paid tiers are not represented — this is acceptable (free tier exists).

### 13.5 Canonical URL

No `<link rel="canonical">` tag found in `index.html`. This is a P3 SEO gap — without canonical, search engines may index both `https://vyomquant.in/` and `https://www.vyomquant.in/` separately.

### 13.6 Favicon

`<link rel="icon" type="image/svg+xml" href="/vite.svg">` — uses the generic **Vite framework favicon**, not a branded VyomQuant favicon. This is visible in browser tabs.

### 13.7 Robots / Sitemap

No `robots.txt` or `sitemap.xml` found in `dist/`. No `<meta name="robots">` tag.

### 13.8 Heading Hierarchy

Active landing page:
- `<h1>`: Hero headline — 1 instance ✅
- `<h2>`: Section headings throughout ✅
- `<h3>`: Card/item headings ✅

No heading hierarchy violations found in active page.

### 13.9 Crawlability

Scroll-target navigation items in navbar and footer (Features, Security, Pricing, FAQ) use `<button onClick>` rather than `<a href="#section-id">` anchor links. Search engine crawlers cannot follow these buttons to discover section content as linked resources. This is a minor SEO consideration.

---

## 14. LEGAL / TRUST CLAIM REVIEW

### 14.1 Risk Disclaimer — Footer (SUPPORTED)

The `Footer.jsx` includes a risk disclaimer (lines 224–228):
> "Algorithmic trading involves substantial risk of loss. Past performance of backtests does not guarantee future results. VyomQuant provides software infrastructure only; all execution decisions are made by the user. Paper trading mode is enabled by default. VyomQuant is not a registered investment adviser. Not financial advice."

This disclaimer is **present and appropriate**. ✅

### 14.2 Legacy Footer Copyright (MISMATCH)

`src/pages/Landing.jsx` line 541:
> "© {year} VyomQuant. Simulated paper trading beta platform."

This footer is in the **orphaned** `Landing.jsx` only. The active `Footer.jsx` correctly says:
> "© {year} VyomQuant. All rights reserved."

**Not a live issue** — but the orphaned file should be cleaned up.

### 14.3 Claims Requiring Qualification

| Claim | Flag |
|:---|:---|
| "Institutional-grade" (multiple locations) | Marketing qualifier — no independent certification |
| "sub-millisecond latency" | Unsubstantiated performance claim (see Product Truth #10) |
| "Argon2id password hashing" | Potentially inaccurate — Supabase uses bcrypt by default |
| "Immutable audit trails" | "Immutable" implies database-level write-once constraints not verified |
| "Defense-in-depth architecture" | Marketing description — not independently verified |
| AI assistance in hero subheadline | AICopilot is DORMANT — claim is inaccurate for current state |

### 14.4 Legal Page Routes

All four legal routes are properly defined:
- `/legal/privacy` → `LegalPage type="privacy"` ✅
- `/legal/terms` → `LegalPage type="terms"` ✅
- `/legal/risk` → `LegalPage type="risk"` ✅
- `/legal/refund` → `LegalPage type="refund"` ✅

Footer links use `<Link to="/legal/privacy">` etc. — correct. ✅

---

## 15. SECRET SCAN

### 15.1 Source Code Scan

| Pattern | Files Searched | Result |
|:---|:---|:---|
| JWTs (`eyJhb...`) | All landing components + `index.html` | CLEAN |
| `access_token`, `refresh_token` | Landing components | CLEAN (only used as variable names in auth flow, no values) |
| `service_role`, `SUPABASE_SERVICE_ROLE` | All landing + dist | CLEAN |
| API keys, passwords | All landing components | CLEAN |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | Referenced via `import.meta.env` only | SAFE (anon key is intentionally public) |

### 15.2 Production Bundle Scan

| Asset | Pattern | Result |
|:---|:---|:---|
| `LandingPage-ePOD8spG.js` | JWT, service_role, SUPABASE | CLEAN |
| `index-Bxq81Ijm.js` | access_token, refresh_token, API_KEY, password | CLEAN (strings appear only as code logic, no hardcoded values) |

### 15.3 Secret Classification

No real secrets were discovered. All environment variable references are either:
- `PLACEHOLDER` (unset in local dev, injected at build/deploy time)
- `SAFE_PUBLIC` (anon key — designed to be exposed in browser clients per Supabase architecture)

---

## 16. TEST RESULTS

### 16.1 Backend Auth Regression (Phase 7D Baseline)

```
pytest tests/test_phase7b_auth_remediation.py tests/test_admin_auth.py
       tests/test_role_granularity_and_audit.py tests/test_mfa_security_lifecycle.py
       -v --tb=short

Result: 33 passed, 34 warnings in 76.57s — EXIT CODE 0
```

### 16.2 Frontend Tests (Vitest)

```
Test Files:  1 failed | 44 passed (45)
     Tests:  1 failed | 1035 passed (1036)
  Start at:  08:09:08
  Duration:  359.05s (transform 5.74s, setup 0ms, import 94.72s, tests 165.78s)
  Exit code: 1
```

**One pre-existing test failure (unrelated to landing page):**

| File | Test | Failure |
|:---|:---|:---|
| `tests/unit/portfolio-rendering.test.jsx:147` | Portfolio rendering snapshot | `expect(portfolioContent).toMatch(/Trade history/i)` — string not found in rendered component |

This failure is in the `Portfolio.jsx` rendering test, not related to the landing page surface. It indicates a component text mismatch that predates Phase 8A. **Not introduced during this audit.**

**Landing page has no dedicated Vitest test coverage.** No test file for `LandingPage.jsx`, `Hero.jsx`, `Navbar.jsx`, `Pricing.jsx`, `Footer.jsx`, or any other landing component was found.

### 16.3 Production Frontend Build

`npm run build` (Phase 7D) — **Exit code 0, built in 2m 34s** — PASS.

---

## 17. PROTECTED BOUNDARY VERIFICATION

### 17.1 Phase 8A Modifications

**Zero source code files were modified during Phase 8A.** This was a read-only audit.

### 17.2 Protected File Integrity

| Protected File | Phase 8A Status |
|:---|:---:|
| `backend_app/routers/admin.py` | Unmodified ✅ |
| `backend_app/routers/copilot.py` | Unmodified ✅ |
| `algo22-terminal/src/components/admin/AdminDashboard.jsx` | Unmodified ✅ (inspected) |
| `algo22-terminal/src/pages/Dashboard.jsx` | Unmodified ✅ |
| `algo22-terminal/src/pages/Profile.jsx` | Unmodified ✅ |
| `algo22-terminal/src/pages/ExchangeManager.jsx` | Unmodified ✅ |
| `backend_app/core/dependencies.py` | Unmodified ✅ |
| `backend_app/core/auth_middleware.py` | Unmodified ✅ |
| Phase 7B auth remediation | Intact ✅ |

**Git status: clean** (1 untracked `.kiro/` spec directory — not source code).

---

## 18. FINDINGS MATRIX

| ID | Severity | Surface | Finding | Evidence | Impact | Phase 8B Recommendation |
|:---|:---:|:---|:---|:---|:---|:---|
| **F-01** | **P2** | Authorization | `/admin/waitlist` route has no frontend auth gate — `AdminDashboard` renders without authentication check | `App.jsx` line 271; `waitlistAdminApi.getAll()` uses anon client | Potential exposure of all waitlist entries (names, emails, Telegram handles) if Supabase RLS is not strictly configured | Add `AuthGuard` + admin role check to `/admin/waitlist` route, or gate with `GuestGuard` + Supabase admin session |
| **F-02** | **P3** | Product Truth | "AI assistance" claimed in hero subheadline; `AICopilot.jsx` is explicitly DORMANT/UNMOUNTED | `Hero.jsx` line 56; `AICopilot.jsx` header comment | Misrepresents current platform capabilities | Remove "AI assistance" from hero subheadline or restore Copilot section |
| **F-03** | **P3** | Product Truth | "sub-millisecond latency" execution claim is unsubstantiated | `ModernTradingSection.jsx` line 9 | Potential misleading performance promise; consumer protection concern | Replace with qualified claim, e.g., "low-latency execution via CCXT.pro WebSocket" |
| **F-04** | **P3** | Product Truth | "Argon2id password hashing" security claim may be inaccurate (Supabase uses bcrypt by default) | `SecuritySection.jsx` line 29 | Inaccurate security claim visible to users evaluating trustworthiness | Verify Supabase's actual hashing algorithm and update or remove claim |
| **F-05** | **P3** | Product Truth | "LIVE EXECUTION" badge in hero DAG mockup is a static illustration shown without disclaimer | `Hero.jsx` line 140 | May mislead visitors into believing the mockup shows live data; platform has an active FREEZE flag on live execution | Add "Illustrative" label or remove the LIVE EXECUTION badge from the static mockup |
| **F-06** | **P3** | UX | Waitlist section ("closed beta, reviewed in 48h") directly contradicts immediate `/signup` conversion path | `Waitlist.jsx`; Pricing CTAs | Visitor cannot determine whether immediate access is available or requires waitlist approval — conversion friction | Unify: either remove waitlist section if immediate signup is live, or gate Pricing CTAs to waitlist |
| **F-07** | **P3** | UX/SEO | Footer "Documentation" link routes to `/docs` — no route exists; catch-all redirects to `/` | `Footer.jsx` line 61; `App.jsx` line 323 | Broken link damages trust; removes crawlable internal link | Remove link or create a `/docs` route |
| **F-08** | **P3** | SEO | No `<link rel="canonical">` tag; no `robots.txt` or `sitemap.xml` | `index.html`, `dist/` | Duplicate content indexing risk; search engines cannot discover sitemap | Add canonical link, robots.txt, and sitemap.xml |
| **F-09** | **P3** | Legal / Trust | "Immutable audit trails" claim is a strong assurance without verifiable evidence | `SecuritySection.jsx` line 24 | Over-promises security guarantee | Qualify: "comprehensive audit logging" or verify DB-level immutability |
| **F-10** | **INFO** | Architecture | Two landing page implementations: active `components/landing/LandingPage.jsx` and orphaned `pages/Landing.jsx` (not mounted, 631 lines with testimonials, old design) | `App.jsx` line 21 route definition | Developer confusion risk; orphaned file contains outdated product claims | Remove or archive `pages/Landing.jsx` |
| **F-11** | **INFO** | Product Truth | Hero backtest metrics (Sharpe: 2.1, Win Rate: 67.4%) are hardcoded illustrative values with no disclaimer | `Hero.jsx` lines 185–192 | Visitors may interpret as real performance data | Add "Illustrative" label beneath the DAG mockup stats |
| **F-12** | **INFO** | UX | "Launch App" / "Launch Web App" CTAs bounce unauthenticated users to `/signin` without explanation; label implies instant access | `Navbar.jsx` line 112, `Hero.jsx` line 63, `FinalCTA.jsx` line 45 | Conversion friction for first-time visitors | Change CTAs to "Sign In" or "Get Started" for unauthenticated context |
| **F-13** | **INFO** | SEO | Favicon is generic Vite SVG (`/vite.svg`), not a branded VyomQuant icon | `index.html` line 6 | Brand trust deficit in browser tabs and bookmarks | Replace with VyomQuant branded favicon |

---

## 19. RECOMMENDED PHASE 8B PLAN

### Priority Order

**P2 — Blocking for Production Beta**
1. Add auth guard to `/admin/waitlist` route (F-01)

**P3 — High Priority for Go-Live**
2. Remove "AI assistance" from hero subheadline or restore Copilot (F-02)
3. Replace "sub-millisecond latency" with qualified execution claim (F-03)
4. Verify and correct "Argon2id" password hashing claim (F-04)
5. Add "Illustrative" disclaimer to hero DAG mockup LIVE EXECUTION badge (F-05)
6. Resolve Waitlist vs. Signup flow conflict (F-06)
7. Fix footer "Documentation" broken link (F-07)

**P3 — Pre-Launch SEO/Trust**
8. Add canonical tag, robots.txt, sitemap.xml (F-08)
9. Qualify "Immutable audit trails" claim (F-09)

**INFO — Cleanup / Polish**
10. Remove or archive orphaned `pages/Landing.jsx` (F-10)
11. Add "Illustrative" label to hero backtest metrics (F-11)
12. Reconsider "Launch App" CTA label for unauthenticated context (F-12)
13. Replace Vite favicon with VyomQuant branded favicon (F-13)

**Phase 8B Additional Scope (Not in Findings)**
- Add real product screenshots to `ScreenshotsSection.jsx`
- Verify Supabase RLS configuration on `waitlist` table
- Implement Vitest frontend test suite or confirm test configuration
- Verify `/legal/risk` content for accuracy against actual risk disclosures
- Verify macOS/Windows installer binaries exist and are functional at `/download`
- Resolve footer company anchor links (`#about`, `#contact`, `#careers`) — add sections or remove links

---

## 20. FINAL VERDICT

### Phase 8A Verdict: CONDITIONAL PASS

**Zero P0 or P1 security or authorization vulnerabilities discovered.**

The authentication boundary between the public landing page and the authenticated application is correctly implemented via `AuthGuard` and `GuestGuard`. No credentials, secrets, or JWTs are exposed in landing page source or production bundles. All redirect paths are internal. No open redirect vectors exist.

**One P2 finding (F-01):** The `/admin/waitlist` route is publicly accessible without any frontend authentication gate. The actual data exposure depends on Supabase RLS configuration, but the absence of a route-level guard is a structural defect requiring remediation before sustained production traffic.

**Seven P3 and five INFO findings** cover product truth accuracy, UX conversion friction, SEO gaps, and orphaned code. None are security-blocking.

**Authorization for Phase 8B:** Phase 8B Landing Page Remediation may proceed with explicit user authorization.

---

*End of PHASE_8A_LANDING_PAGE_FORENSIC_AUDIT.md*
*Audit executed under read-only zero-modification protocol.*
*Protected boundaries verified intact. No source modifications made during Phase 8A.*
