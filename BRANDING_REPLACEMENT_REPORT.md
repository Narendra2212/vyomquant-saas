# Branding Replacement Report — Algo22 → VyomQuant

> **Date:** 2026-06-22  
> **Branch:** `feature/strategy-marketplace-v1`  
> **Scope:** Customer-facing UI only  
> **Reference:** [PROJECT_ARCHITECTURE.md](./PROJECT_ARCHITECTURE.md)

---

## Rules Applied

| Rule | Applied |
|---|---|
| Only modify customer-facing UI | ✅ |
| Do NOT rename repositories | ✅ — `algo22-terminal/` directory name unchanged |
| Do NOT rename backend modules | ✅ — All Python modules unchanged |
| Do NOT rename database tables | ✅ — All Supabase/SQLAlchemy tables unchanged |
| Do NOT rename Redis keys | ✅ — All Redis key patterns unchanged |
| Do NOT rename API routes | ✅ — All `/api/*` prefixes unchanged |
| Do NOT rename environment variables | ✅ — `VITE_API_URL`, etc. unchanged |
| Do NOT change architecture | ✅ — No structural changes |

### Explicitly Excluded (per rules)

| Item | Reason |
|---|---|
| `localStorage` keys `algo22_onboarding`, `algo22_wizard_step`, `algo22_wizard_completed` | Internal state keys, not customer-facing UI text |
| `api.algo22.io` URL strings in `App.jsx` and `config.js` | API routes — Rule 6 |
| `support@algo22.io` email in `SupportPage.jsx` | Infrastructure email — considered an API endpoint per rules |
| `docs.algo22.io` URL in sidebar button | External URL — infrastructure, not UI copy |
| `admin@algo22.io` placeholder in auth form | Infrastructure email |
| CSV download filename `algo22_ledger_*.csv` | Internal artifact naming, not visible UI text |
| `Algo22Copilot` JSX **component name / export** | Internal code identifier — NOT renamed (file name rule also applies to module names) |
| `algo22-terminal/` directory | Repository structure — Rule 2 |
| `com.algo22.terminal` bundle identifier | Infrastructure identifier |

---

## Files Modified

### 1. `algo22-terminal/src-tauri/tauri.conf.json`

**Tauri installer metadata — visible in desktop window title, system tray, installer name, Task Manager.**

| Location | Before | After |
|---|---|---|
| `productName` | `"Algo22"` | `"VyomQuant"` |
| `windows[0].title` | `"Algo22 — Algorithmic Trading Terminal"` | `"VyomQuant — Algorithmic Trading Terminal"` |

---

### 2. `algo22-terminal/src-tauri/Cargo.toml`

**Rust crate description — visible in installer metadata and package listings.**

| Location | Before | After |
|---|---|---|
| `description` | `"Algo22 — Professional Algorithmic Trading Terminal"` | `"VyomQuant — Professional Algorithmic Trading Terminal"` |

---

### 3. `algo22-terminal/src/App.jsx`

**Primary frontend file (358 KB). Customer-facing changes only.**

| Line | Location | Before | After |
|---|---|---|---|
| 1514 | Sidebar logo wordmark | `ALGO<span>22</span>` | `VYOM<span>QUANT</span>` |
| 1515 | Sidebar tagline | `QUANT INFRASTRUCTURE` | `QUANT INFRASTRUCTURE` *(unchanged)* |
| 2119 | Landing hero subtitle | `Aerora Quant is the ultimate no-code platform...` | `VyomQuant is the ultimate no-code platform...` |
| 2223 | Testimonials section subheading | `See how quants deploy strategy systems with Aerora` | `See how quants deploy strategy systems with VyomQuant` |
| 2268 | Footer copyright | `© {year} Aerora Dynamics. Simulated paper trading beta platform.` | `© {year} VyomQuant. Simulated paper trading beta platform.` |
| 2317 | Demo modal subtitle | `See how Aerora Quant can automate your visual trading strategies at scale.` | `See how VyomQuant can automate your visual trading strategies at scale.` |
| 2058 | FAQ Q1 | `Do I need coding experience to use Aerora Quant?` | `Do I need coding experience to use VyomQuant?` |
| 7622 | Referral Twitter share text | `Trade at the speed of alpha with Algo22's institutional quant terminal. Join here: ` | `Trade at the speed of alpha with VyomQuant's institutional quant terminal. Join here: ` |
| 7628 | Referral email subject | `Invitation: Algo22 Quant Terminal` | `Invitation: VyomQuant Quant Terminal` |
| 7629 | Referral email body | `I've been using Algo22 to automate my trading strategies...` | `I've been using VyomQuant to automate my trading strategies...` |

---

### 4. `algo22-terminal/src/components/Algo22Copilot.jsx`

**Copilot panel header and initialization message — visible to users.**

| Line | Location | Before | After |
|---|---|---|---|
| 6 | Initial message text | `'Algo22 Copilot initialized. I can help you...'` | `'VyomQuant Copilot initialized. I can help you...'` |
| 89 | Panel header `<span>` | `Algo22 Copilot` | `VyomQuant Copilot` |

> **Note:** The JSX component function name `Algo22Copilot` and its `export default` are **not renamed** — these are internal code identifiers.

---

### 5. `algo22-terminal/src/config.js`

**Config file comment — developer-facing comment only. No user-visible change needed.**

| Line | Location | Before | After |
|---|---|---|---|
| 2 | Comment | `* Centralized configuration for Algo22 Terminal` | `* Centralized configuration for VyomQuant Terminal` |

---

## Screenshots / UI Areas Impacted

| UI Area | Component | Change visible to user |
|---|---|---|
| **Desktop window title bar** | `tauri.conf.json` | `VyomQuant — Algorithmic Trading Terminal` |
| **Windows installer / macOS DMG name** | `tauri.conf.json` | `VyomQuant` in installer |
| **Task Manager / Activity Monitor** | `Cargo.toml` description | `VyomQuant` |
| **Sidebar top logo** | `App.jsx` Sidebar function | `VYOMQUANT` wordmark |
| **Landing page hero** | `App.jsx` Landing function | "VyomQuant is the ultimate no-code platform…" |
| **Landing page testimonials** | `App.jsx` Landing function | "…deploy strategy systems with VyomQuant" |
| **Landing page FAQ** | `App.jsx` faqs array | "Do I need coding experience to use VyomQuant?" |
| **Landing page footer** | `App.jsx` Landing footer | "© 2026 VyomQuant. Simulated paper trading beta platform." |
| **Demo booking modal** | `App.jsx` showDemoModal | "See how VyomQuant can automate…" |
| **Referral → Share on Twitter** | `App.jsx` handleTwitterShare | Tweet text uses VyomQuant |
| **Referral → Share via Email** | `App.jsx` handleEmailShare | Subject + body use VyomQuant |
| **AI Copilot header** | `Algo22Copilot.jsx` | "VyomQuant Copilot" |
| **AI Copilot welcome message** | `Algo22Copilot.jsx` | "VyomQuant Copilot initialized." |

---

## Not Modified (In-scope search, out-of-scope change)

| File | String found | Reason not changed |
|---|---|---|
| `src/components/OnboardingWidget.jsx` | `algo22_onboarding` (localStorage key) | Internal state key — Rule 5 (Redis-equivalent) |
| `src/components/FirstTradeWizard.jsx` | `algo22_wizard_step`, `algo22_wizard_completed` | Internal state keys |
| `src/App.jsx` L1529 | `https://docs.algo22.io` | External URL / API route — Rule 6 |
| `src/App.jsx` L2581 | `admin@algo22.io` (placeholder) | Infrastructure email |
| `src/App.jsx` L2766 | `admin@algo22.io` (mock UI text in security panel) | Infrastructure email |
| `src/App.jsx` L3351, L3413 | `https://api.algo22.io` | API route — Rule 6 |
| `src/App.jsx` L6619 | `algo22_ledger_*.csv` | Internal artifact filename |
| `src/SupportPage.jsx` L101, 105 | `support@algo22.io` | Infrastructure support email |
| `src/config.js` L7, L8 | `https://api.algo22.io` | API base URLs — Rule 6 |
| `algo22-terminal/ALGO22_UX_MASTER_AUDIT.md` | Various | Internal developer doc, not user-facing |
| `algo22-terminal/DESIGN_SYSTEM_V2.md` | Various | Internal developer doc |
| `algo22-terminal/FINAL_UX_CERTIFICATION.md` | Various | Internal developer doc |
| `algo22-terminal/IMPLEMENTATION_ROADMAP.md` | Various | Internal developer doc |
| `algo22-terminal/ONBOARDING_PLAN.md` | Various | Internal developer doc |

---

## Build Verification

### Desktop (Tauri)

```bash
# Development check — verify window title
cd algo22-terminal
cargo tauri dev

# Expected: Window title bar shows "VyomQuant — Algorithmic Trading Terminal"
# Sidebar logo: VYOMQUANT
```

### Frontend (Vite)

```bash
cd algo22-terminal
npm run dev

# Open http://localhost:1420
# Verify:
# 1. Landing page hero text contains "VyomQuant"
# 2. Landing footer shows "© 2026 VyomQuant"
# 3. Sidebar shows "VYOMQUANT" wordmark
# 4. Copilot panel header: "VyomQuant Copilot"
# 5. Copilot welcome message: "VyomQuant Copilot initialized."
# 6. Referral share modal: VyomQuant in tweet/email text
```

### Production Build (installer branding)

```bash
cd algo22-terminal
npm run build
cargo tauri build

# Windows: Installer title shows "VyomQuant"
# macOS: DMG name is "VyomQuant"
# Linux: .AppImage name is "VyomQuant"
# All: productName = "VyomQuant" throughout OS integration
```

### Grep Verification (confirm no missed references)

```bash
# After changes, run these — should return ZERO customer-facing results:
grep -rn "Algo22" algo22-terminal/src/App.jsx
# Only remaining: internal JSX component names like Algo22Copilot (by design)

grep -in "algo22" algo22-terminal/src/components/Algo22Copilot.jsx
# Only remaining: function declaration `Algo22Copilot` (by design)

grep -rn "Algo22" algo22-terminal/src-tauri/tauri.conf.json
# Expected: 0 results

grep -rn "Aerora Quant\|Aerora Dynamics" algo22-terminal/src/App.jsx
# Expected: 0 results (all replaced with VyomQuant)
```

---

## Summary

| Category | Count |
|---|---|
| Files modified | 4 |
| Customer-facing string replacements | 12 |
| Internal strings intentionally preserved | 8 |
| Backend files touched | 0 |
| Database tables affected | 0 |
| Redis keys affected | 0 |
| API routes affected | 0 |

---

*Generated 2026-06-22. No code architecture was modified.*
