# Requirements Document

Retail UI Simplification — `algo22-terminal`

## Introduction

VyomQuant is a retail crypto algo-trading SaaS. Its frontend is `algo22-terminal/` (React 18 + Vite + Tailwind CSS v4, deployed by `.github/workflows/06-frontend-deploy.yml`). The `vyomquant-ui-redesign` spec built a design convention and applied it to eleven pages; `production-launch-hardening` then made trader-visible figures explicitly nullable so that an absent value is distinguishable from a zero. Both passes optimised for correctness in front of a quant reader.

This spec addresses a different reader. The request that opened it is that the pages are hard for a retail algo-trading customer to understand, that Marketplace is visibly an older generation of the product, that the landing page is broken, and that none of this may change what the product does.

The document's central claim is that the perceived complexity has **three distinct causes with three different remedies**, and that treating them as one problem is how a simplification pass turns into a regression:

| Cause | Evidence | Remedy | Requirements |
| --- | --- | --- | --- |
| **Typography was never centralised.** Colour literals were ratcheted to zero on eleven pages; font size never was. 451 absolute font sizes survive across 14 page files in three different syntaxes. | §1.2, §1.3 | Extend the existing token layer and add the missing ratchet. Mechanical. | 1, 2, 3 |
| **Eleven of twenty-two pages never adopted the convention** — no `usePanelState`, no `pageFields`, no `ds/*`, and outside the accessibility ratchet's enforcement entirely. Marketplace is a twelfth case: inside the enforced set, but styled from the previous generation. | §1.4, §1.6 | Adopt what exists. This is **migration, not redesign**. | 4, 5, 6, 10, 11, 12 |
| **The eleven migrated pages are consistent but verbose and dense.** Live Trading carries 3,175 characters of standing prose in 29 constants; the environment badge renders `ENVIRONMENT UNCONFIRMED` at 10px monospace bold caps; Paper Trading renders multi-sentence help text at 9px. | §1.5, §1.7 | Copy and visual hierarchy. **Not** migration, **not** new components. | 7, 8, 9 |

The landing page is handled separately and honestly: the symptom has not been described and has not been reproduced, and static reading did not find it. Requirement 13 records that state rather than inventing a defect list.

---

## Severity scale

| Tier | Meaning | Blocks the pass |
| --- | --- | --- |
| **P0** | A change that would alter what the product does, weaken a safety distinction the previous two passes established, or break an existing guard. These are constraints on the work, not work items. | Yes — a violation reverts the change |
| **P1** | A retail reader cannot read or operate the surface: text below the legibility floor, a page that ignores the reader's font-size preference, a page from a visibly different product generation, an unreachable control. | Yes |
| **P2** | The surface is readable but noisier, denser, or more verbose than a retail reader needs. Measurable, but the target is partly a judgement. | No — tracked |
| **P3** | Dead code and latent conditions no trader currently reaches. | No |

## Evidence status

Every clause carries one tag. The distinction is load-bearing: this document asks for changes to things whose "correct" value is partly a matter of taste, and it is only checkable where it separates the two.

- **[MEASURED]** — counted in this tree during requirements gathering, by the command recorded alongside it. Reproducible.
- **[JUDGEMENT]** — the *direction* is asserted (less is better for this reader); the *target* is a chosen number, not a derived one. Each such clause names an objective proxy so the acceptance test is a count rather than an opinion, and says plainly that the threshold is negotiable and the proxy is not.
- **[UNVERIFIED]** — the condition is asserted by the requester and has not been reproduced. Closing the clause starts with a reproduction, not with a change.

No clause may be closed by assertion. Where a measurement contradicts the framing this pass was given, §1.11 records the contradiction rather than the framing.

---

## Glossary

- **Terminal**: `algo22-terminal/` — the live frontend. The only frontend this spec touches.
- **Duplicate_Terminal**: `aerora_quant_platform/frontend_app/algo22-terminal` — a stale copy, present in the tree, recorded as a deployment hazard by `production-launch-hardening` clause 1.35. Never modified by this spec.
- **Token_Layer**: `src/styles/tokens.css` (the Tailwind v4 `@theme` source) and its generated mirror `src/design/tokens.js`.
- **Type_Scale**: the seven `--text-*` steps declared in `Token_Layer` — `micro` 0.625rem, `small` 0.6875rem, `body` 0.8125rem, `title` 0.875rem, `section` 1.125rem, `page` 1.5rem, `figure` 1.75rem.
- **Convention**: the four modules `vyomquant-ui-redesign` established and this spec adopts without extending — `src/design/pageFields.js` (every trader-visible field declared with its source path and its not-available reason), `src/hooks/usePanelState.js` (eight panel states: `idle`, `loading`, `ready`, `refreshing`, `empty`, `error`, `unavailable`, `unauthorised`), `src/components/ds/*` (the primitives), and `src/design/errorCopy.js` + `src/design/reported.js` (the copy and availability layers).
- **In_Scope_Pages**: the eleven files enumerated as `IN_SCOPE_PAGES` in `tests/unit/guards/a11y-ratchet.test.js` — `Dashboard`, `LiveTrading`, `Strategies`, `StrategyDetail`, `StrategyBuilder`, `Backtester`, `SignalTrace`, `Portfolio`, `TradeHistory`, `PaperTrading`, `StrategyMarketplace`. These lint at `jsx-a11y` **error**.
- **Unmigrated_Pages**: the eleven page files under `src/pages/` that are *not* in `In_Scope_Pages` — `Profile`, `ExchangeManager`, `Billing`, `AuthPage`, `RiskSettings`, `TwoFA`, `Wizard`, `SecurityLogs`, `LegalPage`, `UpdatePasswordPage`, `Landing`.
- **Landing_Surface**: `src/components/landing/LandingPage.jsx` and the 13 section components it renders. **This, not `src/pages/Landing.jsx`, is the page served at `/`** (`src/App.jsx:44`, `:590`).
- **Absolute_Font_Size**: a font size expressed in device pixels rather than as a `Type_Scale` step. Three syntaxes occur: unitless `fontSize: 11` (React coerces to px), quoted `fontSize: '14px'`, and the Tailwind arbitrary value `text-[10px]`.
- **Legibility_Floor**: `--text-micro` (0.625rem). The smallest step the `Type_Scale` declares, annotated in `tokens.css` for chips and labels.
- **Standing_Prose**: user-facing sentences rendered unconditionally on a page — caveats, notes and explanations that are present whether or not the trader asked for them. Distinguished from prose behind a disclosure, a tooltip, or an error state.
- **Guard_Suites**: `tests/unit/guards/` (16 files, 186 `it`/`test` declarations) and `tests/unit/design/` (14 files, 200 declarations plus 17 `.each` tables). `tests/unit/ds/` adds 26 files and 533 declarations. All must stay green.
- **Availability_Distinction**: the property `production-launch-hardening` established — a genuine `0.0` renders as `0.0`, and an unavailable figure renders the not-available marker carrying the reason declared for it in `pageFields.js`. Implemented by `design/reported.js` (`available`, `unavailable`, `fromNullable`, `UNREPORTED_REASON`).

---

## Current state, as measured

Stated here because every requirement below is a consequence of one of these numbers.

### 1.1 The page inventory **[MEASURED]**

Twenty-two files under `src/pages/`, 32,346 lines. Counts are: total lines; `Absolute_Font_Size` by syntax; inline `monospace` in a `fontFamily`; `font-mono` as a class; occurrences of `uppercase` (which subsumes `textTransform: 'uppercase'`).

| Page | Lines | `fontSize: N` | `'Npx'` | `text-[Npx]` | `monospace` | `font-mono` | upper | Group |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| StrategyBuilder | 4609 | 0 | 0 | 0 | 14 | 0 | 13 | In scope |
| PaperTrading | 3527 | 43 | 0 | 0 | 22 | 0 | 9 | In scope |
| LiveTrading | 3115 | 0 | 0 | 0 | 0 | 3 | 7 | In scope |
| Dashboard | 2730 | 0 | 0 | 0 | 0 | 5 | 4 | In scope |
| Strategies | 2622 | 0 | 0 | 0 | 0 | 9 | 0 | In scope |
| SignalTrace | 2308 | 0 | 0 | 0 | 0 | 10 | 4 | In scope |
| Backtester | 2146 | 0 | 0 | 0 | 0 | 1 | 0 | In scope |
| StrategyDetail | 1786 | 61 | 0 | 0 | 28 | 1 | 3 | In scope |
| Portfolio | 1425 | 0 | 0 | 0 | 0 | 1 | 2 | In scope |
| **StrategyMarketplace** | 1210 | 0 | 0 | **30** | 0 | **69** | **25** | In scope |
| AuthPage | 1181 | 29 | 0 | 0 | 29 | 0 | 5 | Unmigrated |
| Profile | 999 | 79 | 0 | 0 | 51 | 0 | 0 | Unmigrated |
| ExchangeManager | 905 | 52 | 0 | 0 | 1 | 0 | 0 | Unmigrated |
| Billing | 840 | 39 | 0 | 0 | 28 | 0 | 3 | Unmigrated |
| TradeHistory | 800 | 0 | 0 | 0 | 0 | 1 | 2 | In scope |
| Landing *(unmounted)* | 644 | 36 | 5 | 0 | 25 | 0 | 8 | Unmigrated |
| RiskSettings | 438 | 16 | 0 | 0 | 10 | 0 | 3 | Unmigrated |
| TwoFA | 397 | 12 | 0 | 0 | 10 | 0 | 0 | Unmigrated |
| Wizard | 250 | 24 | 0 | 0 | 13 | 0 | 2 | Unmigrated |
| SecurityLogs | 189 | 7 | 0 | 0 | 6 | 0 | 2 | Unmigrated |
| LegalPage | 146 | 0 | **15** | 0 | 2 | 0 | 12 | Unmigrated |
| UpdatePasswordPage | 79 | 3 | 0 | 0 | 0 | 0 | 0 | Unmigrated |
| **Total** | **32346** | **401** | **20** | **30** | **239** | **100** | **104** | |

### 1.2 Colour was ratcheted. Font size was not. **[MEASURED]**

`tests/unit/guards/no-colour-literals.budget.js` is a checked-in per-file count asserted **exactly**, in both directions — over budget means a literal was added, under budget means the budget was not lowered when literals were removed. Eleven pages sit at `0` and are held there. Ten still carry literals:

`ExchangeManager` 128, `RiskSettings` 53, `AuthPage` 30, `Landing` 18, `TwoFA` 12, `Billing` 9, `Profile` 7, `Wizard` 5, `LegalPage` 3, `SecurityLogs` 3 — **268 total**, all on `Unmigrated_Pages`.

No equivalent budget exists for font size. Searching `tests/unit/guards/` and `tests/unit/design/` for `fontSize`, `font-size` or `text-[` returns nothing. The four budget files are `api-paths`, `no-colour-literals`, `no-native-dialogs`, `no-placeholders`.

That asymmetry is the mechanism. The redesign drove one axis to zero on eleven pages and enforced it; the other axis was left to each page's author, and 451 absolute sizes are the result.

### 1.3 Absolute sizes ignore the reader's font-size preference **[MEASURED]**

All 451 are device-pixel values. A trader who has raised their browser's default font size — the ordinary accommodation for presbyopia, the most common visual condition in the 40+ demographic a paid trading product sells to — sees no change on any of the 14 files that carry them. The `Type_Scale` is declared in `rem` precisely so that it does scale; the pages that bypass it opt out of that.

### 1.4 The three groups are not a binary split **[MEASURED]**

| Group | Pages | `usePanelState`/`pageFields` refs | `ds/*` imports | a11y enforcement | Colour literals |
| --- | --- | --- | --- | --- | --- |
| Adopted | LiveTrading 33, Dashboard 30, Backtester 20, SignalTrace 16, Portfolio 14, TradeHistory 10, Strategies 9 | 9–33 | 10–14 | error, 0 findings | 0 |
| Partial | PaperTrading (1 ref, 1 `ds` import, colour tokens adopted, `Type_Scale` not), StrategyBuilder (0 refs, 3 imports), StrategyDetail (0 refs, 4 imports) | 0–1 | 1–4 | error, 0 findings | 0 |
| Not adopted | the eleven `Unmigrated_Pages`, plus `StrategyMarketplace` as its own case | 0 | 0 | **not linted at all** | 268 |

The framing this pass was given described two causes. The measurement shows three, and the middle group matters: Paper Trading and Strategy Detail are inside the enforced set and at zero colour literals, yet carry 104 of the 401 unitless sizes between them. A remedy keyed on "unmigrated pages" would skip them.

### 1.5 Paper Trading renders explanatory prose below the floor **[MEASURED]**

`PaperTrading.jsx`'s 43 unitless sizes are `8` ×1, `9` ×23, `10` ×8, `11` ×10, `16` ×1, plus one conditional `missing ? 13 : 18` and one `'1.25rem'`. **41 of 43 are at or below 11px, and 23 are at 9px.**

9px is not confined to labels. `labelStyle` (`:433`), `thStyle` (`:459`), `tdStyle` (`:477`) and `stackedLabelStyle` (`:502`) are labels. But `:2887`, `:3285`, `:3409`, `:3436` and `:3490` are multi-sentence explanations of substantive behaviour — that a negative latency means the clocks disagree, that three series are distinguishable without relying on colour, that no price is carried forward and none is synthesised. Those sentences exist because `production-launch-hardening` required the page to explain its own honesty. They are set at 9px, 36% below the `Legibility_Floor`.

### 1.6 Marketplace is the previous generation, and its debt is not colour **[MEASURED]**

`StrategyMarketplace.jsx` is at `0` colour literals — it uses Tailwind token classes throughout. Its distance from the current generation is on four other axes:

- **30 `text-[Npx]` arbitrary classes.** The only file in `src/pages/` with any. Each bypasses the `Type_Scale` while looking like a token class.
- **69 `font-mono` and 25 `uppercase` class usages** in 1,210 lines — the highest density of both on any page, by a factor of roughly seven over the next-highest.
- **Decorative styling the `Token_Layer` does not declare**: three `bg-gradient-to-*` fills (`:665`, `:667`, `:1032`), three `rounded-2xl` (16px, above the scale's `--radius-xl: 12px` ceiling), one `blur-3xl` glow (`:1034`), and a `Sparkles`-iconned "Featured" ribbon. `tokens.css` declares no gradient and no coloured glow; `vyomquant-ui-redesign` §3.2 states the reason ("no coloured glows. Calm by default").
- **The only outstanding accessibility waiver.** `eslint-rules/a11y-ratchet.js:227` holds exactly one entry: `'src/pages/StrategyMarketplace.jsx': { count: 4, task: '26.1' }`. Every other page in `In_Scope_Pages` has had its entry deleted after reaching zero.

Its read path is also the only one on an `In_Scope_Page` still outside the `Convention`: `:381` is `catch (err) { console.error(err); setError('Failed to load marketplace data. Please try again.'); }` — one string for every failure mode, no `usePanelState`, no `errorCopy.js`.

### 1.7 The migrated pages are consistent and verbose **[MEASURED] proxy, [JUDGEMENT] target**

Counting top-level `const NAME = "…"` string constants that reach the screen:

| Page | `Standing_Prose` constants | Characters | Longest |
| --- | --- | --- | --- |
| LiveTrading | 29 | 3175 | `STOP_DESCRIPTION`, 539 |
| SignalTrace | 14 | 1351 | `REASON_NO_EXCHANGE_RESPONSE`, 253 |
| Dashboard | 13 | 1225 | `CHIP_CLASSES`, 449 |
| Portfolio | 5 | 774 | — |
| TradeHistory | 3 | 584 | — |
| Strategies | 7 | 420 | — |
| StrategyMarketplace | 3 | 186 | — |
| Backtester | 3 | 157 | — |

`LiveTrading.jsx:1324`'s `REGISTRY_CAVEAT` is 384 characters across four sentences, rendered unconditionally above the deployment list in every state that has rows or an absence to explain. Its content is correct and important — the in-process registry genuinely can omit a deployment started by an earlier worker, and a trader who reads the list as exhaustive can double-deploy. The problem is placement and weight, not truth. `SELECTION_CAVEAT` (242 characters) sits beside it.

`Dashboard.jsx` renders 7 `ds/Panel` instances, 6 of which carry an empty-state branch. On a fresh account all 6 resolve to `empty` simultaneously, at equal weight, with no single element marked as the next thing to do.

### 1.8 The badge treatment is set in the primitive, not on the page **[MEASURED]**

`ds/TradingEnvironmentBadge.jsx:230` renders every variant with `font-mono font-bold uppercase tracking-wide` at `text-micro`. So `ENVIRONMENT UNCONFIRMED` — 23 characters — reaches the screen as 10px monospace bold all-caps with added letter spacing, on Dashboard, Portfolio, LiveTrading, SignalTrace, StrategyDetail, PaperTrading, `ds/Panel`, `ds/PageHeader`, `ds/StrategyStatus` and `ds/ConfirmDialog`.

Two consequences shape Requirement 9. First, the wording is deliberate and documented in eight separate files: the server genuinely returns `null` for `positions[].environment`, and `semantic.js:236` records that defaulting to LIVE is alarmist while defaulting to PAPER is dangerous, so `null` is rendered rather than resolved. **The distinction must survive any rewording.** Second, because the treatment lives in the primitive, changing it is one edit that lands on eleven pages — which makes it cheap, and makes it something that must be verified against `tests/unit/ds/` rather than eyeballed per page.

### 1.9 The landing page: what is known, and what is not **[MEASURED] / [UNVERIFIED]**

Known:

- `src/pages/Landing.jsx` carries the header `DEPRECATED / UNMOUNTED — LEGACY LANDING PAGE` and is routed nowhere. `src/App.jsx:44` lazy-imports `./components/landing/LandingPage`; `:590` routes it at `/`.
- `Landing_Surface` is `LandingPage.jsx` plus 13 sections: `Navbar`, `Hero`, `TrustSection`, `ScreenshotsSection`, `HowItWorks`, `ModernTradingSection`, `SecuritySection`, `FounderSection`, `DownloadSection`, `Pricing`, `FAQ`, `Waitlist`, `FinalCTA`, `Footer`.
- Eight further files in `src/components/landing/` are imported by nothing: `AICopilot`, `BacktestingDemo`, `Features`, `MetricsBar`, `PaperTradingDemo`, `PortfolioAnalytics`, `ScreenshotComingSoon`, `StrategyBuilderDemo`. The literal `Screenshot Coming Soon` in `ScreenshotComingSoon.jsx:38` therefore does **not** reach the screen.
- No `Landing_Surface` section imports `design/tokens` or `components/ds` directly. `DownloadSection` reaches `ds/Panel` through `components/download/PlatformArtifact`, added by `production-launch-hardening` task 4.3.
- The legacy utility classes the sections use — `accent-cyan`, `text-muted`, `accent-cyan-dim` — **do** resolve: `tokens.css:150`, `:152` and `:162` keep them as aliases onto `--color-brand-*` and `--color-content-*`. They are not broken.
- 16 `text-[Npx]` arbitrary classes (`ScreenshotsSection` 12, `Hero` 2, `DownloadSection` 1, `HowItWorks` 1) and 9 gradient usages.

Not known: **the symptom.** No description was given, no reproduction exists, and static reading of all 14 rendered sections did not find a render-blocking fault. The two recent changes the requester recalls — six missing imports plus an unused `useNavigate`, and the INR-only pricing conversion — are recorded against `src/pages/Landing.jsx`, the unmounted file, so if that is where they landed they changed nothing a visitor sees. Requirement 13 is therefore a reproduction requirement, not a fix requirement.

### 1.10 Context that is not a requirement of this spec

Recorded so a later reader does not attribute either to a visual cause.

- **Marketplace fails in production for a data-layer reason.** `library_strategies.price_minor` is added by `backend_app/migrations/007_marketplace_submissions.sql:1281` (`ADD COLUMN IF NOT EXISTS price_minor`). With 007 unapplied, the projection raises PostgreSQL `42703` (undefined column) and the page shows `Failed to load marketplace data`. **No visual change fixes this.** The Strategies-page equivalent — `strategies.description`, same error class — was fixed at commit `0edb68a`. Requirement 11 improves how the failure is *reported*; applying the migration belongs to whichever spec owns the deploy.
- `production-launch-hardening` clause 1.35 records `Duplicate_Terminal` as a deployment hazard. Requirement 21 keeps this spec out of it.

### 1.11 Where the measurement contradicted the brief **[MEASURED]**

Recorded because the corrected version is what the requirements above are built on, and a later reader comparing this document to the brief that produced it should find the difference explained rather than silent.

| The brief said | The tree says | Effect on this document |
| --- | --- | --- |
| The landing page to rectify is `pages/Landing.jsx`; six missing imports and an unused `useNavigate` were fixed there, and pricing was made INR-only. | `pages/Landing.jsx` is headed `DEPRECATED / UNMOUNTED` and is routed nowhere. `App.jsx:44`/`:590` serve `components/landing/LandingPage.jsx` and its 13 sections. | Requirement 13 targets `Landing_Surface`. Requirement 14.2 stops the ratchets spending 41 font sizes and 18 colour literals on a dead file. **If the earlier fixes landed in `pages/Landing.jsx`, they changed nothing a visitor sees** — which is itself a candidate explanation for "still broken". |
| Marketplace is the only page still using gradients. | `StrategyMarketplace` 3, `Landing` (unmounted) 4, `AuthPage` 3, `Billing` 2, and 9 across `Landing_Surface`. | Requirement 10.1 still names Marketplace's three. Requirements 4 and 15.3 pick up `AuthPage`, `Billing` and the landing surface, which the "only page" framing would have left out. |
| Marketplace has not adopted the convention. | True of `usePanelState`, `pageFields` and `ds/*` — but it is at **0** colour literals and uses token utility classes throughout. Its debt is 30 `text-[Npx]`, 69 `font-mono`, 25 `uppercase`, three gradients, one blur, and 4 a11y findings. | Requirement 10 names the four real axes. Treating it as a colour-token migration would have found nothing to do. |
| Twelve pages never adopted the convention; the migrated ones are merely verbose. | Three groups, not two. `PaperTrading` (1 reference), `StrategyBuilder` (0) and `StrategyDetail` (0) are inside the enforced set and at zero colour literals, yet hold 104 of the 401 unitless sizes. | Requirement 4.6 exists solely because a remedy scoped by the phrase "unmigrated pages" would skip them. |
| Paper Trading's inline sizes are 8, 9, 10, 11, 13, 16, 18. | The value set is right. The distribution is the finding: `9` ×23, `11` ×10, `10` ×8, `8` ×1, `16` ×1, and `13`/`18` only as the two arms of one conditional. **41 of 43 are at or below 11px.** | Requirements 2.3 and 2.4 are written against the distribution, and single out the 9px *prose* rather than the 9px labels. |
| `guards/` and `design/` are ~191 existing tests. | 16 + 14 files carrying 186 + 200 `it`/`test` declarations plus 17 `.each` tables — 386 declarations, roughly double. `tests/unit/ds/` adds 26 files and 533 more, and is the suite that actually covers Requirement 9's primitive edit. | Requirement 18 cites all three directories. `ds/` was absent from the brief and is the one that matters most for the badge rewording. |
| Marketplace shows "Failed to load marketplace data" because migration `007` is unapplied. | Confirmed — `007_marketplace_submissions.sql:1281` adds `price_minor`. | Kept as §1.10 context, not a requirement. Requirement 11.5 states explicitly that this spec does not fix it. |

One item in the brief was not reproducible as described: the Dashboard rendering "~10 competing empty panels". `Dashboard.jsx` renders 7 `ds/Panel` instances, 6 with an empty branch. Requirement 8.2 is written to the measured 7-and-6 rather than the remembered ten. Separately, the brief's `⊗ LIVE` label does not exist as a literal anywhere in `src/`; the badge composes a lucide icon with its text, and §1.8 describes the treatment that is actually in the file.

---

## Requirements

### Requirement 1: One type scale, enforced the way colour is

**Priority: P1.** **[MEASURED]**

**User Story:** As a retail trader, I want text sizes to be consistent across pages, so that I can tell a heading from a label from a figure without relearning each screen.

#### Acceptance Criteria

1. THE `Token_Layer` SHALL remain the only place a font size is declared, and no new size step SHALL be added to satisfy an existing call site — a call site whose size is not in the `Type_Scale` SHALL move to the nearest step. Adding an eighth step to preserve a page's current appearance is the defect this requirement removes, not a way of satisfying it.
2. WHEN a page under `src/pages/` or `src/components/landing/` renders text, THE `Terminal` SHALL express its size as a `Type_Scale` step, and SHALL NOT express it as an `Absolute_Font_Size` in any of the three syntaxes (`fontSize: N`, `fontSize: 'Npx'`, `text-[Npx]`).
3. THE repository SHALL carry a per-file `Absolute_Font_Size` budget under `tests/unit/guards/`, asserted **exactly** in both directions, constructed the same way `no-colour-literals.budget.js` is and sharing its `source-scan.js` helper. An over-count SHALL fail as a new violation; an under-count SHALL fail as a budget not lowered in the commit that cleared it.
4. THE budget SHALL be seeded at the counts in §1.1 — `Profile` 79, `StrategyDetail` 61, `ExchangeManager` 52, `PaperTrading` 43, `Billing` 39, `Landing` 41 (36 unitless + 5 quoted), `StrategyMarketplace` 30, `AuthPage` 29, `Wizard` 24, `RiskSettings` 16, `LegalPage` 15, `TwoFA` 12, `SecurityLogs` 7, `UpdatePasswordPage` 3, `ScreenshotsSection` 12, `Hero` 2, `DownloadSection` 1, `HowItWorks` 1 — and SHALL be lowered by the commit that clears each file, never widened.
5. WHERE a file reaches `0`, ITS entry SHALL be deleted rather than left at `0`, so that a file with no entry is a file held at zero by default and a reintroduced size fails without anyone having to notice. This is the discipline `no-colour-literals.budget.js` documents at its `pages/Dashboard.jsx: 0` entry, inverted for this axis because there is no partially-cleared middle state worth recording.

### Requirement 2: Text scales with the reader's browser preference, and nothing renders below the floor

**Priority: P1.** **[MEASURED]** for the counts, **[JUDGEMENT]** for the floor being the right floor.

**User Story:** As a trader who has raised my browser's default font size, I want the product to honour that, so that I can read my own positions without zooming the whole page.

#### Acceptance Criteria

1. WHEN the browser's default font size is changed from 16px to 20px, THE `Terminal` SHALL render every text element on every page under `src/pages/` and `src/components/landing/` at a proportionally larger size. No element SHALL be pinned to a device-pixel value.
2. THE `Terminal` SHALL NOT render any text below the `Legibility_Floor` (`--text-micro`, 0.625rem).
3. WHEN `PaperTrading.jsx` renders the explanatory copy at `:2887`, `:3285`, `:3409`, `:3436` and `:3490`, THE `Terminal` SHALL render it at `--text-body` or larger, because it is prose in sentences and not a label. The 9px label styles at `:433`, `:459`, `:477` and `:502` SHALL move to `--text-micro`, and the 8px pill at `:558` SHALL move to `--text-micro`.
4. THE 41 of `PaperTrading.jsx`'s 43 sizes that sit at or below 11px SHALL be resolved to `--text-micro`, `--text-small` or `--text-body` according to whether each is a chip, a table cell, or a sentence — and the resolution SHALL be recorded per call site, so that the choice is reviewable rather than uniform.
5. WHERE removing a size below the floor causes a layout to overflow, THE layout SHALL change and the size SHALL NOT. An 8px chip that only fits because it is 8px is a layout defect wearing a typography mask.

*Note on the floor:* 0.625rem (10px at default settings) is the smallest step the `Type_Scale` already declares and `tokens.css` already annotates for chips and labels. It is adopted here rather than derived. A stricter floor is defensible and a looser one is not; the count in 2.2 is the checkable part.

### Requirement 3: Monospace and all-caps confined to what they are for

**Priority: P2.** **[MEASURED]** for the counts, **[JUDGEMENT]** for the rule.

**User Story:** As a retail trader rather than a terminal user, I want sentences to read like sentences, so that the product does not feel like a tool I am unqualified to operate.

#### Acceptance Criteria

1. THE `Terminal` SHALL render monospace only for values whose character alignment or literalness carries meaning — numerals in a column, an identifier, a symbol, a server-supplied code, a timestamp — and SHALL render prose in the sans stack.
2. THE 239 inline `fontFamily: 'monospace'` declarations and the 100 `font-mono` class usages in §1.1 SHALL each be classified as value or prose, and the prose cases SHALL be converted. The classification SHALL be recorded, because the counts alone do not distinguish the two and an unrecorded sweep is indistinguishable from a blanket removal.
3. THE `Terminal` SHALL NOT apply `textTransform: 'uppercase'` or the `uppercase` class to a string longer than three words. All-caps suppresses word-shape recognition, which is the mechanism a reader uses to skim; a two-word chip loses little and a four-word label loses the skim.
4. WHERE `StrategyMarketplace.jsx` accounts for 69 `font-mono` and 25 `uppercase` usages in 1,210 lines, ITS conversion SHALL be handled as part of Requirement 10 rather than separately, because on that page the typography and the visual generation are the same edit.

### Requirement 4: The eleven unmigrated pages adopt the existing convention

**Priority: P1.** **[MEASURED]**

**User Story:** As a retail trader, I want Profile, Billing, Exchanges and Risk Settings to behave like the pages I already learned, so that moving between them does not feel like moving between products.

#### Acceptance Criteria

1. WHEN a page in `Unmigrated_Pages` performs a read, THE page SHALL resolve that read through `usePanelState`, rendering exactly one of its eight states, and SHALL NOT hold its own ad-hoc `loading`/`error` boolean pair.
2. WHEN a page in `Unmigrated_Pages` renders a trader-visible figure, THAT figure SHALL have an entry in `design/pageFields.js` declaring its source path and its not-available reason, and the page SHALL render it through `design/reported.js` rather than reading the response body directly.
3. WHEN a page in `Unmigrated_Pages` renders a status indicator, metric, table, empty condition, loading condition or error condition, IT SHALL use the corresponding `components/ds/*` primitive.
4. THE adoption SHALL add no new primitive to `components/ds/*`, no new state to `usePanelState`, and no new module to `src/design/`. IF a page appears to need one, THEN the need SHALL be recorded and raised rather than satisfied, because a primitive added for one page is how the second convention starts.
5. THE eleven pages SHALL be migrated one page per change, each independently shippable, in the order `RiskSettings` → `TwoFA` → `SecurityLogs` → `UpdatePasswordPage` → `Wizard` → `LegalPage` → `Profile` → `Billing` → `ExchangeManager` → `AuthPage` → (`Landing.jsx`, per Requirement 14). Smallest first, so the convention's fit is tested on a 438-line page before a 1,181-line one.
6. WHERE `PaperTrading.jsx` (1 convention reference), `StrategyBuilder.jsx` (0) and `StrategyDetail.jsx` (0) are inside `In_Scope_Pages` but carry 104 of the 401 unitless sizes, THEY SHALL be held to 4.1 through 4.4 as well. A remedy scoped to "unmigrated pages" by name would skip them; §1.4 is why this clause exists.

### Requirement 5: Colour literals on the remaining pages reach zero

**Priority: P2.** **[MEASURED]**

**User Story:** As a trader, I want a colour to mean the same thing on every page, so that green never means one thing on Dashboard and another on Exchanges.

#### Acceptance Criteria

1. WHEN a page in `Unmigrated_Pages` expresses a colour, IT SHALL read it from `design/tokens.js` or a token utility class, and SHALL NOT author a hex, `rgb()` or `hsl()` literal.
2. THE 268 literals SHALL be removed in the same per-page change as that page's Requirement 4 migration, and `no-colour-literals.budget.js` SHALL be lowered in that same commit — `ExchangeManager` 128, `RiskSettings` 53, `AuthPage` 30, `Landing` 18, `TwoFA` 12, `Billing` 9, `Profile` 7, `Wizard` 5, `LegalPage` 3, `SecurityLogs` 3.
3. WHERE a literal has no semantic equivalent in `design/semantic.js`, THE mapping question SHALL be raised rather than resolved by picking the nearest token. `semantic.js` is the only module that knows which token means "profitable"; a page guessing is the drift this closes.

### Requirement 6: Accessibility enforcement covers every page a trader reaches

**Priority: P1.** **[MEASURED]**

**User Story:** As a trader who navigates by keyboard, I want to change a risk limit or a password without a mouse, so that the pages outside the flagship workflow are not the ones that exclude me.

#### Acceptance Criteria

1. THE `IN_SCOPE_PAGES` list in `tests/unit/guards/a11y-ratchet.test.js` SHALL be extended to include every file under `src/pages/` and the 14 rendered `Landing_Surface` sections, so that `jsx-a11y` lints them at `error`.
2. WHERE a page has outstanding findings when it joins the list, ITS count SHALL be recorded in `eslint-rules/a11y-ratchet.js` with the change that will clear it, and the entry SHALL be deleted — not lowered to `0` — when it reaches zero. This is the existing guard's documented rule and it is restated because this requirement is the first thing to add entries to that list since it was emptied.
3. `src/components/ds/**` SHALL remain at exactly zero findings.
4. WHEN a control's visual weight is reduced by any change in this spec, THE control SHALL keep its accessible name and its keyboard path. A control that becomes an icon SHALL retain a text alternative; a control that moves behind a disclosure SHALL remain reachable by keyboard from the page's tab order.
5. THE `RiskSettings.jsx` kill-switch controls SHALL be verified keyboard-operable before and after their migration, and the verification SHALL be a test rather than a manual check, because a kill switch that is only reachable by mouse is the one control where the gap is not an inconvenience.

### Requirement 7: A standing-prose budget, with the substance kept behind disclosure

**Priority: P2.** **[MEASURED]** proxy, **[JUDGEMENT]** threshold.

**User Story:** As a retail trader opening Live Trading, I want to see what is running before I read about how the list is built, so that the page answers my question before it qualifies its own answer.

#### Acceptance Criteria

1. THE `Terminal` SHALL render no more than 400 characters of `Standing_Prose` above the first data element of any page.
2. WHERE a page's `Standing_Prose` exceeds 7.1's budget, THE surplus SHALL move behind a disclosure — a `ds/Tooltip`, an expandable note, or a `ds/Alert` shown only in the state it describes — and SHALL NOT be deleted or shortened in a way that changes what it asserts.
3. WHEN `LiveTrading.jsx` renders `REGISTRY_CAVEAT` (384 characters, 4 sentences), THE page SHALL surface its operative sentence — that a deployment absent from the list is UNKNOWN rather than stopped — at all times, and SHALL place the explanation of why behind a disclosure. The caveat is true and safety-relevant; this clause moves it, and 7.2 forbids weakening it.
4. THE per-page `Standing_Prose` character count SHALL be asserted by a test seeded at §1.7's counts — `LiveTrading` 3175, `SignalTrace` 1351, `Dashboard` 1225, `Portfolio` 774, `TradeHistory` 584, `Strategies` 420, `StrategyMarketplace` 186, `Backtester` 157 — and SHALL only decrease.
5. THE reduction SHALL NOT be achieved by moving a sentence from a `const` into inline JSX. IF the count falls without the rendered output changing, THEN the clause is not satisfied, and the test SHALL count rendered text rather than source constants for this reason.

*Note on 400:* chosen, not derived. It is roughly one `REGISTRY_CAVEAT` and it puts `Portfolio`, `TradeHistory`, `Strategies`, `StrategyMarketplace` and `Backtester` inside the budget already, which makes it a target the three heaviest pages move toward rather than a rewrite of all eight. The count is the checkable part; the number is open.

### Requirement 8: A new account gets one next action

**Priority: P1.** **[MEASURED]** proxy, **[JUDGEMENT]** target.

**User Story:** As a trader who just signed up, I want the product to tell me the one thing to do first, so that I do not read ten panels explaining what I do not have.

#### Acceptance Criteria

1. WHEN a trader's account has no strategies, no deployments, no positions and no trades, THE `Dashboard_Page` SHALL render exactly one element carrying a primary action, and SHALL render the remaining empty panels at reduced visual weight or collapsed.
2. THE `Dashboard_Page` SHALL NOT render more than 3 simultaneously-visible `empty` panels on a fresh account. It renders 7 `ds/Panel` instances today, 6 of which carry an empty branch.
3. WHERE a panel is empty because the trader has not done something yet, ITS copy SHALL name what to do. WHERE it is empty because a read returned nothing, ITS copy SHALL say so. THE two SHALL remain distinguishable, because `usePanelState` already separates `empty` from `unavailable` and collapsing them would undo it.
4. THE same rule SHALL apply to `Strategies`, `Portfolio`, `TradeHistory` and `LiveTrading` on a fresh account.
5. THE fresh-account state SHALL be verified by a test that renders each page against a zero-data fixture and asserts the count of visible empty panels and the count of primary actions, so that 8.1 and 8.2 are measurements rather than screenshots.

### Requirement 9: Labels written for a retail reader, with their distinctions intact

**Priority: P2.** **[MEASURED]** for the treatment, **[JUDGEMENT]** for the wording.

**User Story:** As a retail trader, I want a field label to tell me what I am looking at in words I use, so that I do not have to infer the product's internal vocabulary.

#### Acceptance Criteria

1. WHEN `ds/TradingEnvironmentBadge` renders a label, IT SHALL NOT combine monospace, bold, all-caps and added letter spacing at `--text-micro` for a string longer than two words. `:230` applies all four to every variant today, so `ENVIRONMENT UNCONFIRMED` renders as 23 characters of 10px monospace bold caps.
2. THE reworded labels SHALL preserve every distinction the current vocabulary draws. Specifically: `ENVIRONMENT UNCONFIRMED` (the server reported no environment) SHALL remain distinguishable from `SIMULATED · SERVER LABEL UNAVAILABLE` (the server reported simulated but did not label the venue), because `TradingEnvironmentBadge.jsx:62` records that the difference is information; and neither SHALL be replaced by a resolved value, because `semantic.js:236` records that defaulting to LIVE is alarmist and defaulting to PAPER is dangerous.
3. THE rewording SHALL be one edit to the primitive, not per-page overrides. `ENVIRONMENT UNCONFIRMED` is spelled in `Panel.jsx:126`, `TradingEnvironmentBadge.jsx:113` and `StrategyStatus.jsx:201`, and referenced in comments across eight further files; three declarations mean one change, and a fourth spelling on a page is a new defect.
4. WHEN a label names a backend concept with no retail equivalent, THE `Terminal` SHALL pair it with a `ds/Tooltip` giving the plain-language reading, and SHALL NOT replace the concept with an approximation.
5. THE 104 `uppercase` occurrences in §1.1 SHALL be reviewed against 3.3's three-word rule as part of this requirement.

### Requirement 10: Marketplace is brought onto the current visual generation

**Priority: P1.** **[MEASURED]**

**User Story:** As a trader, I want Marketplace to look like the rest of the product, so that I do not wonder whether I have left it.

#### Acceptance Criteria

1. `StrategyMarketplace.jsx` SHALL NOT render a `bg-gradient-to-*` fill. THE three at `:665`, `:667` and `:1032` SHALL be replaced with the flat surfaces `tokens.css` declares.
2. `StrategyMarketplace.jsx` SHALL NOT render a radius outside the six the `Token_Layer` declares — `--radius-xs` 2px, `sm` 4px, `md` 6px, `lg` 8px, `xl` 12px, `full` 9999px. THE three `rounded-2xl` (16px) usages SHALL move onto that scale. `rounded-full` is `--radius-full` and is not part of this clause.
3. `StrategyMarketplace.jsx` SHALL NOT render a decorative blur or coloured glow. THE `blur-3xl` at `:1034` SHALL be removed. `vyomquant-ui-redesign` §3.2's elevation tokens carry no coloured glow by decision, and reintroducing one on a single page is the drift this closes.
4. THE 30 `text-[Npx]` classes SHALL move onto the `Type_Scale`, and the 69 `font-mono` and 25 `uppercase` usages SHALL be resolved against Requirement 3.
5. THE "Featured" ribbon SHALL state what featured means, or SHALL be removed. An unexplained promotional badge on a page selling strategies to retail traders is a claim without a basis, and `vyomquant-ui-redesign`'s design.md rejects the gamified direction that produced it.
6. THE page's strategy cards SHALL render through `ds/*` primitives, and the subscription state SHALL continue to render through `design/subscriptionState.js` — which already exists and SHALL NOT be reimplemented.
7. WHERE the rebuild changes which data the page requests, IT SHALL NOT. Requirement 16 governs; this clause exists because a visual rebuild of a 1,210-line page is where a request shape quietly changes.

### Requirement 11: Marketplace reports a failure the way every other page does

**Priority: P1.** **[MEASURED]**

**User Story:** As a trader, I want to know whether Marketplace is empty, broken, or something I am not entitled to see, so that I know whether to retry, wait, or do something else.

#### Acceptance Criteria

1. WHEN `StrategyMarketplace.jsx` performs its catalogue read, IT SHALL resolve it through `usePanelState`, replacing the `loading`/`error` pair and the single string at `:382`.
2. WHEN that read fails, THE page SHALL render copy resolved through `design/errorCopy.js` from the server's own error code, and SHALL distinguish a transport failure, a server fault, an authorisation refusal and an entitlement refusal. `Failed to load marketplace data. Please try again.` SHALL NOT be the response to all four.
3. THE page SHALL NOT render `err.message` or any exception text as user-facing content, and SHALL NOT log the error object to the console in place of handling it. `:381`'s `console.error(err)` SHALL be replaced by the `Convention`'s own reporting path.
4. WHERE the failure is the `42703` undefined-column condition described in §1.10, THE page SHALL render an `unavailable` state naming what cannot be shown. IT SHALL NOT present a schema fault as an empty catalogue, because "no strategies exist" and "we cannot read the catalogue" are different facts and the second one is not the trader's to act on.
5. THIS requirement SHALL NOT be treated as fixing the production Marketplace failure. Applying migration `007` is a data-layer change outside this spec; 11.4 changes only what the trader is told while it is unapplied.

### Requirement 12: The last accessibility waiver is cleared

**Priority: P1.** **[MEASURED]**

**User Story:** As a trader who navigates by keyboard, I want to browse and subscribe to a marketplace strategy without a mouse.

#### Acceptance Criteria

1. THE 4 `jsx-a11y` findings on `src/pages/StrategyMarketplace.jsx` SHALL be resolved.
2. THE waiver entry at `eslint-rules/a11y-ratchet.js:227` SHALL be **deleted**, not lowered to `0`, per the guard's own rule — a cleared page belongs at `error` with every other unwaived page, and deleting the line is what puts it there.
3. WHEN the waiver list becomes empty, `a11y-ratchet.test.js` SHALL still pass with an empty `A11Y_PAGE_WAIVERS`, and the assertion that every non-waived in-scope page is at zero SHALL be the only thing holding the line. IF the empty case is not handled, THEN that is a defect in the guard and SHALL be fixed rather than worked around by leaving a `0` entry.
4. THE strategy cards SHALL be keyboard-activatable. `:664`'s `onClick` on a `div` is one of the four findings, and `ds/DataTable`'s row activation is the precedent the other in-scope pages used.

### Requirement 13: The landing-page defect is reproduced before anything changes

**Priority: P1.** **[UNVERIFIED]**

**User Story:** As a platform owner, I want the reported landing-page breakage identified before it is fixed, so that we do not rewrite a working page and leave the actual fault in place.

#### Acceptance Criteria

1. THE landing-page work SHALL begin with a reproduction recording: the URL, the viewport, the browser, the observed symptom, and whether it reproduces against the deployed bundle, a local build, or both.
2. THE reproduction SHALL be performed against `Landing_Surface` — `src/components/landing/LandingPage.jsx` and its 13 sections — because that is what `App.jsx:590` routes at `/`. `src/pages/Landing.jsx` is unmounted and SHALL NOT be treated as the subject of the report.
3. THE recording SHALL state which of the following the symptom is: a render failure, a layout fault at a specific viewport, a missing or 403 asset, incorrect content, or a navigation failure. Each implies a different fix, and this document asserts none of them.
4. THE following SHALL be recorded as checked and sound, so the reproduction does not re-cover them: the legacy utility classes resolve via `tokens.css:150`/`:152`/`:162`; `ScreenshotComingSoon.jsx`'s placeholder string is unreachable because nothing imports it; `DownloadSection`'s installer links were already withdrawn to `ds/Panel`'s `unavailable` state by `production-launch-hardening` task 4.3.
5. IF no symptom reproduces, THEN the report SHALL say so and the requester SHALL be asked for the specific page state, and Requirements 14 and 15 SHALL proceed on their own merits. A reproduction that fails is a result, not a blocker.
6. WHEN the symptom is identified, IT SHALL be filed as a numbered clause against this document with its own priority, with a regression test that fails before the fix and passes after. No fix SHALL be made under this requirement number.

### Requirement 14: Dead code on the landing surface and in `src/pages/`

**Priority: P3.** **[MEASURED]**

**User Story:** As a contributor, I want the file I am reading to be the file that renders, so that I do not fix the wrong one.

#### Acceptance Criteria

1. THE eight unreferenced components in `src/components/landing/` — `AICopilot`, `BacktestingDemo`, `Features`, `MetricsBar`, `PaperTradingDemo`, `PortfolioAnalytics`, `ScreenshotComingSoon`, `StrategyBuilderDemo` — SHALL be deleted, or SHALL gain a header stating why an unrendered component is retained.
2. `src/pages/Landing.jsx` SHALL be deleted, or SHALL keep its `DEPRECATED / UNMOUNTED` header and SHALL be excluded from the budgets in Requirements 1 and 5 with that exclusion recorded. Counting 41 font sizes and 18 colour literals against a file nothing renders spends the ratchet on nothing.
3. WHERE deletion is chosen, THE change SHALL confirm by search that no import, test or route references the file, and SHALL be its own commit separate from any behavioural change.
4. THE `no-placeholders` guard SHALL be confirmed to cover `src/components/landing/**` after 14.1, so that a future "coming soon" string on the live surface fails rather than passing as it does in an unreferenced file.

### Requirement 15: The landing surface joins the convention

**Priority: P2.** **[MEASURED]**

**User Story:** As a prospective customer, I want the marketing page and the product to look like one product, so that signing up does not feel like arriving somewhere else.

#### Acceptance Criteria

1. THE 14 rendered `Landing_Surface` sections SHALL express colour, spacing, radius, shadow and font size through the `Token_Layer`, and the legacy aliases (`accent-cyan`, `text-muted`, `accent-cyan-dim`) SHALL be replaced by the tokens they alias, so the aliases can eventually be retired from `tokens.css`.
2. THE 16 `text-[Npx]` classes SHALL move onto the `Type_Scale` per Requirement 1.
3. THE 9 gradient usages SHALL be reviewed against Requirement 10.1's rule. WHERE a gradient is judged appropriate on a marketing surface in a way it is not on a trading surface, THAT exception SHALL be declared in `tokens.css` as a named marketing token rather than authored inline, so the exception is visible and countable.
4. THE sections SHALL NOT be restructured, reordered or rewritten under this requirement. IT covers the token layer only. Content and layout changes depend on Requirement 13's reproduction and SHALL be filed separately.
5. THE pricing presentation SHALL remain INR-only with no currency toggle, as it is today. This clause records the current state so that a token migration does not reintroduce a toggle by reverting a file.

---

## Preservation

These are constraints, not work items. Each is a P0 and each governs every requirement above. A change that satisfies a requirement above and violates one of these is reverted, not merged with a note.

### Requirement 16: No functional change

**Priority: P0.** **[MEASURED]** for the surfaces named, and the requester's explicit and primary constraint.

**User Story:** As a trader with real funds on a live exchange key, I want a visual simplification pass to change nothing about what the product does, so that a change made for readability cannot cost me money.

#### Acceptance Criteria

1. THE spec SHALL NOT modify order execution, risk-control, strategy-versioning, authentication, authorisation, entitlement, billing or settlement logic, in the frontend or the backend.
2. THE spec SHALL NOT change which endpoint a page calls, the shape of a request body, a query parameter, a header, a WebSocket channel subscription, or the polling interval of any read.
3. THE spec SHALL NOT change a route path, a redirect, a guard condition, or the set of pages a given account can reach.
4. THE spec SHALL NOT change which fields a page reads from a response, nor add a client-side derivation of a field the server does not send. A field that is absent stays absent and renders per Requirement 19.
5. THE spec SHALL NOT remove, disable or reword a confirmation step. THE deploy confirmation flow, the live-deployment statement that real orders will be placed, and every `ds/ConfirmDialog` SHALL keep their required explicit action. A confirmation is friction on purpose, and "remove complexity" does not reach it.
6. WHERE a simplification appears to require a backend change, IT SHALL be abandoned or raised, and SHALL NOT be approximated on the client. `vyomquant-ui-redesign` Requirement 19.2 is the precedent; this spec has no backend change register and SHALL NOT acquire one.
7. THE `git diff` of every change in this spec SHALL be confined to presentation: JSX structure, class names, style objects, copy strings, and the token and guard files this document names. A diff touching `src/api/`, `src/websocketClient.js`, or anything under `backend_app/` SHALL be treated as out of scope regardless of its content.

### Requirement 17: No second design system

**Priority: P0.** **[MEASURED]**

**User Story:** As a contributor, I want this pass to adopt the convention that already exists rather than start a parallel one, so that the next reader has one place to look instead of two.

#### Acceptance Criteria

1. THE spec SHALL NOT create a new token file, a new `design/` module, a new primitive directory, or a page-local token object. `src/styles/tokens.css`, `src/design/tokens.js`, `src/design/semantic.js`, `src/design/pageFields.js`, `src/design/errorCopy.js`, `src/design/reported.js`, `src/design/subscriptionState.js`, `src/design/notificationPolicy.js`, `src/design/alertCondition.js`, `src/design/errorLine.js` and `src/design/pageHierarchy.js` already exist and are adopted as they are.
2. THE spec SHALL NOT add a state to `usePanelState`'s eight, nor a variant to a `ds/*` primitive solely to reproduce a page's current appearance.
3. THE spec SHALL NOT add a primitive to `components/ds/*`. IF a page cannot be built from the existing set, THEN that is a finding to raise, and the page SHALL wait.
4. THE `no-local-tokens.test.js` guard SHALL stay green. A page-local `C` object or equivalent is the specific failure mode this clause exists to prevent, and the redesign's §1.1 records four generations of styling that arose exactly this way.
5. WHERE this spec and `vyomquant-ui-redesign`'s design.md disagree, THAT design.md governs, and the disagreement SHALL be recorded rather than resolved silently in code. This spec adopts a convention; it does not amend one.

### Requirement 18: Every existing guard and design suite stays green

**Priority: P0.** **[MEASURED]**

**User Story:** As a platform owner, I want the guarantees the previous two passes established to be asserted rather than assumed through this one, so that preservation is a test result and not a claim.

#### Acceptance Criteria

1. `tests/unit/guards/` (16 files, 186 declarations) and `tests/unit/design/` (14 files, 200 declarations plus 17 `.each` tables) SHALL pass after every change in this spec.
2. `tests/unit/ds/` (26 files, 533 declarations plus 9 `.each` tables) SHALL pass after every change, and SHALL be the verification for any edit to a `ds/*` primitive — including Requirement 9's badge rewording, which lands on eleven pages from one file.
3. THE budgets `no-colour-literals`, `no-placeholders`, `no-native-dialogs` and `api-paths` SHALL only decrease. `api-paths.budget.js` in particular SHALL NOT change at all, because a change there means Requirement 16.2 was violated.
4. `dead-tailwind.test.js`, `nav-contract.test.js` and `tokens.generated.test.js` SHALL pass. THE last of these fails when `design/tokens.js` is stale relative to `tokens.css`, which is the failure mode Requirement 1's token work will encounter first.
5. THE full frontend suite SHALL NOT be run unscoped — `production-launch-hardening` records it exceeding 25 minutes. Verification SHALL be scoped to the directories a change touches plus the guard and design suites.
6. WHERE a guard fails because a count moved in the intended direction, THE budget SHALL be lowered in the same commit. A failing guard SHALL NOT be skipped, widened, or moved into a follow-up.

### Requirement 19: A genuine zero still renders zero, and an unavailable figure keeps its reason

**Priority: P0.** **[MEASURED]**

**User Story:** As a trader, I want to keep being able to tell "this figure is zero" from "we cannot read this figure", so that a decluttered screen does not hide a failed read behind a plausible number.

#### Acceptance Criteria

1. WHEN a trader-visible figure is a genuine `0`, `0.0` or empty collection, THE `Terminal` SHALL render it as that value.
2. WHEN a trader-visible figure is unavailable, THE `Terminal` SHALL render the not-available marker together with the reason declared for that field in `design/pageFields.js`.
3. THE spec SHALL NOT remove a `reason` string, shorten one to a marker alone, or replace a set of reasons with one generic sentence, in order to reduce clutter. `pageFields.js` states the point directly — "not available" with no reason tells a trader exactly as much as a blank cell — and `production-launch-hardening` clauses 2.1 through 2.6 exist because the codebase previously substituted `100000.0` and `0.0` for failed reads. **Collapsing a reason re-introduces the defect that pass removed.**
4. THE spec SHALL NOT collapse `usePanelState`'s `empty` and `unavailable`, nor its `error` and `unavailable`, in pursuit of fewer states on screen. The states are three different facts about the trader's account.
5. THE spec SHALL NOT substitute a fabricated, hardcoded, or previously-cached value for any figure while a read is failing. A denser layout SHALL NOT be achieved by removing the element that says a value is missing.
6. WHERE Requirement 7 moves an explanation behind a disclosure, THE explanation SHALL remain reachable and unchanged in substance. Moving is permitted; deleting and paraphrasing-away are not, and 7.2 is the clause that draws that line.
7. THE `design/reported.js` and `design/pageFields.js` test suites SHALL pass unchanged, and Property P5 — that a failed read renders no figure and no zero — SHALL continue to hold across every declared entry.

### Requirement 20: Accessibility does not regress

**Priority: P0.** **[MEASURED]**

**User Story:** As a trader who uses a keyboard or a screen reader, I want minimalism to mean less visual noise rather than fewer ways to reach a control, so that a calmer product is not a less operable one.

#### Acceptance Criteria

1. WHEN a control's visual prominence is reduced, ITS accessible name SHALL be retained. An icon-only button SHALL carry an `aria-label` or visually hidden text.
2. WHEN content moves behind a disclosure, THE disclosure SHALL be a keyboard-operable control with an accessible name and a programmatic expanded state, and the content SHALL be reachable through it.
3. THE keyboard paths the redesign established SHALL be retained, including the `RiskSettings` kill-switch toggles named in Requirement 6.5.
4. `src/components/ds/**` SHALL remain at exactly zero `jsx-a11y` findings, and no page SHALL gain a finding. THE waiver list SHALL only shrink under Requirement 12, and only grow under Requirement 6.2 when previously unlinted pages join the enforced set.
5. WHEN a colour or a border is removed for calmness, THE state it carried SHALL remain expressed in text or shape. No state SHALL become colour-only, and no state SHALL become nothing.
6. THE focus indicator declared once in `tokens.css`'s `:focus-visible` block SHALL remain visible on every control after restyling.

### Requirement 21: The duplicate frontend is never touched

**Priority: P1.** **[MEASURED]**

**User Story:** As a contributor running a repository-wide edit, I want the stale copy of the frontend to stay untouched, so that a bulk typography change does not land in a tree nothing deploys.

#### Acceptance Criteria

1. NO change in this spec SHALL modify any file under `aerora_quant_platform/frontend_app/algo22-terminal`.
2. WHERE a grep or a codemod would match a path under that directory, IT SHALL be excluded explicitly. The tree contains a near-identical `src/pages/` there, and a bulk font-size or colour-literal edit is precisely the kind of change that lands in both.
3. THE `git diff` of every change SHALL be confirmed to contain no path under that directory before commit.

### Requirement 22: Per-page, independently shippable increments

**Priority: P2.** **[JUDGEMENT]**

**User Story:** As a platform owner, I want the product shippable after every commit in this pass, so that a regression is one revert away and a bisect lands on one page.

#### Acceptance Criteria

1. EACH page SHALL be changed in its own commit, and the application SHALL be shippable after each.
2. EACH commit SHALL lower whichever budget it cleared, in the same commit, so that the budget files never describe a state the tree has left.
3. NO commit SHALL mix a token-layer change with a page change. THE `tokens.generated.test.js` staleness check makes the first kind of change global, and bundling it with a page edit makes a bisect useless.
4. THE order SHALL be: the missing font-size ratchet (Requirement 1.3, seeded at today's counts and therefore green on the first commit) → the primitive-level typography changes (Requirements 3, 9) → Marketplace (Requirements 10, 11, 12) → the `Unmigrated_Pages` in Requirement 4.5's order → the `Landing_Surface` (Requirements 14, 15), gated on Requirement 13. Seeding the ratchet first is what makes every later commit's reduction visible.

---

## Out of scope

Named explicitly so that a later reader does not find the omission and treat it as an oversight.

1. **Any backend change.** No route, schema, migration, service or model. This includes applying migration `007`, which is what actually fixes the production Marketplace failure (§1.10).
2. **New features, new pages, new navigation entries, new charts, new columns.** The set of things a trader can do is unchanged.
3. **Removing a capability to reduce complexity.** A control that is confusing is reworded or moved, never deleted. Deleting a documented capability is a functional change and Requirement 16 forbids it.
4. **The 295 open Dependabot alerts, the eslint error backlog, and the bundle-size question.** Owned by `production-launch-hardening`.
5. **The `Strategy_Builder` canvas interaction model.** `StrategyBuilder.jsx` is 4,609 lines and its graph editing is a spec of its own. This document holds it to Requirements 1, 3 and 4.6 — typography and convention only — and touches no canvas behaviour.
6. **Mobile layout.** `DesktopOnlyOverlay` gates below 1000px today. Whether that should change is a separate question and this spec does not answer it.
7. **Retiring the legacy `tokens.css` aliases.** Requirement 15.1 stops new use of them; deleting them is a later change gated on every consumer having moved.
8. **Performance.** No claim is made that fewer rendered characters make a page faster, and no performance target is set.
9. **The eight gamification/addictiveness audit documents** superseded by `vyomquant-ui-redesign`. They remain superseded; this spec does not revisit them.
10. **`Duplicate_Terminal`.** Requirement 21.

---

## Requirement index

| # | Requirement | Priority | Evidence | Primary files |
| --- | --- | --- | --- | --- |
| 1 | One type scale, enforced | P1 | MEASURED | `tokens.css`, new `*.budget.js`, 18 files |
| 2 | Relative sizing and a legibility floor | P1 | MEASURED / JUDGEMENT | `PaperTrading.jsx`, 14 page files |
| 3 | Monospace and all-caps confined | P2 | MEASURED / JUDGEMENT | 13 page files, 20 landing files |
| 4 | Unmigrated pages adopt the convention | P1 | MEASURED | the 11 `Unmigrated_Pages` + `PaperTrading`, `StrategyBuilder`, `StrategyDetail` |
| 5 | Colour literals reach zero | P2 | MEASURED | `no-colour-literals.budget.js`, 10 pages |
| 6 | Accessibility enforcement extended | P1 | MEASURED | `a11y-ratchet.test.js`, `a11y-ratchet.js`, `RiskSettings.jsx` |
| 7 | Standing-prose budget | P2 | MEASURED / JUDGEMENT | `LiveTrading.jsx`, `SignalTrace.jsx`, `Dashboard.jsx` |
| 8 | One next action for a new account | P1 | MEASURED / JUDGEMENT | `Dashboard.jsx`, `Strategies.jsx`, `Portfolio.jsx`, `TradeHistory.jsx`, `LiveTrading.jsx` |
| 9 | Retail-readable labels | P2 | MEASURED / JUDGEMENT | `ds/TradingEnvironmentBadge.jsx`, `ds/Panel.jsx`, `ds/StrategyStatus.jsx` |
| 10 | Marketplace visual generation | P1 | MEASURED | `StrategyMarketplace.jsx` |
| 11 | Marketplace read and error states | P1 | MEASURED | `StrategyMarketplace.jsx`, `design/errorCopy.js` |
| 12 | Marketplace a11y waiver cleared | P1 | MEASURED | `StrategyMarketplace.jsx`, `a11y-ratchet.js` |
| 13 | Landing defect reproduced first | P1 | **UNVERIFIED** | `components/landing/*` |
| 14 | Landing and pages dead code | P3 | MEASURED | 8 landing components, `pages/Landing.jsx` |
| 15 | Landing joins the convention | P2 | MEASURED | 14 `Landing_Surface` files |
| 16 | No functional change | **P0** | requester constraint | all |
| 17 | No second design system | **P0** | MEASURED | `src/design/*`, `components/ds/*` |
| 18 | Existing suites stay green | **P0** | MEASURED | `tests/unit/{guards,design,ds}/` |
| 19 | Zero vs unavailable preserved | **P0** | MEASURED | `design/reported.js`, `design/pageFields.js` |
| 20 | Accessibility does not regress | **P0** | MEASURED | `a11y-ratchet`, `ds/*`, `tokens.css` |
| 21 | Duplicate frontend untouched | P1 | MEASURED | `aerora_quant_platform/frontend_app/` |
| 22 | Per-page shippable increments | P2 | JUDGEMENT | all |

**22 requirements: 5 P0, 10 P1, 6 P2, 1 P3.**
