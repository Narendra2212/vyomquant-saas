# Task 17 — light mode: the blocker is one line in the token generator

**Date:** 2026-11-16 · `main` @ `878cec87`
**Finding:** light mode is blocked by a single **deliberate, documented** decision in
`scripts/gen-tokens.mjs`, not by the breadth of the UI. Reversing it correctly makes 900+
call sites theme-aware with **no call-site changes at all**.
**Status:** measured, not started. This is one focused session's work and the measurement
below is the part that makes it cheap.

---

## 1. The good news first

`src/styles/tokens.css` is a genuine single source of truth and says so:

> *No other file in this repository may declare a colour, radius, shadow, transition,
> breakpoint, z-index or type-scale value.*

107 tokens, semantic names (`--color-surface-*`, `--color-content-*`, `--color-status-*`,
`--color-env-*`), **contrast ratios recorded per token**, and one token explicitly marked
non-text-only at 3.2:1. Tailwind v4 `@theme static` emits all of them unconditionally, with a
measured reason for `static` (plain `@theme` tree-shook 56 of 89 at tailwindcss 4.2.4).

Anything styled through a Tailwind utility — `bg-surface-panel`, `text-content-primary` —
already resolves through `var()` and would follow a theme override for free.

## 2. The blocker

`src/design/tokens.js` is **generated** from that CSS, and the generator records this decision
at `gen-tokens.mjs:103`:

> *DECISION: the JS mirror carries the RESOLVED LITERAL, not the var() reference. … reading
> `token.compat.accent.profit` needs a usable colour — a `'var(--…)'` string [would not work].*

So `token.surface.panel` is the string `'#0F1117'`, fixed at build time. Every inline style
that reads it gets a dark hex baked in, and **no `[data-theme="light"]` CSS block can reach
it** — the value was resolved before the browser ever saw a theme attribute.

### How much depends on it — measured

`token.<colour>.` occurrences in `src/`, excluding tests and the generated file:

| File | Hits |
|---|---|
| `components/SupportCenter.jsx` | 197 |
| `pages/PaperTrading.jsx` | 134 |
| `pages/StrategyBuilder.jsx` | 125 |
| `pages/StrategyDetail.jsx` | 114 |
| `components/ds/primitives.jsx` | 88 |
| `components/NotificationCenter.jsx` | 78 |
| `components/ResearchConsole.jsx` | 60 |
| `components/DeploymentConsole.jsx` | 38 |
| `pages/TwoFA.jsx` | 33 |
| …and ~20 more | |

**900+ in total.** That is the number Task 17 is really about, and it is why *"avoid isolated
components that remain stuck in the previous theme"* is the requirement most likely to be
missed: every one of these is a component that would stay dark.

## 3. Why sweeping the call sites is the wrong fix

The instinct is to convert 900 inline styles to Tailwind utilities. That is months of work, it
touches every page, and it would be reviewed as a redesign rather than a theming change.

**The call sites are already correct.** They read a semantic token by name, which is exactly
what they should do. What is wrong is one layer below them: the token resolves to a literal
instead of a reference.

## 4. The fix, and the part of the decision that was right

Emit `var(--color-…)` for the colour groups, so `token.surface.panel` becomes
`'var(--color-surface-panel)'`. Inline styles accept `var()` wherever they accept a colour, so
all 900 sites become theme-aware **unchanged**. The helper already exists —
`export const cssVar = (path) => \`var(--${path.replace(/\./g, '-')})\`` — so the shape is
established; it is the default that needs inverting.

**But the original decision was not wrong, it was narrow.** Some consumers genuinely need a
real value, and shipping `var()` to them would break them silently. Before changing the
default, enumerate them:

1. **`token.compat.*`** — named in the decision itself. Audit each consumer.
2. **Any colour arithmetic.** `tokens.css` already records that
   `` `${token.line.default}15` `` — a token with two hex digits concatenated on, a hand-mixed
   alpha wearing a token's name — existed and was removed as a defect. A `var()` default makes
   that class of bug *fail loudly* instead of silently producing an invalid colour, which is
   an improvement, but every existing instance must be found first.
3. **`ds/Chart` / recharts.** SVG `fill`/`stroke` accept `var()` in current browsers, but
   recharts computes some colours in JS. This needs checking per series prop, and it is the
   one place where getting it wrong is invisible until a chart renders blank.
4. **Canvas, if any.** `CanvasRenderingContext2D` does **not** resolve `var()`.

The right shape is therefore two exports: `token` emitting references, and a narrow
`tokenLiteral` for the enumerated consumers above — not a blanket switch.

## 5. Remaining work after that

1. **A `[data-theme="light"]` block in `tokens.css`** redefining the ~40 colour tokens.
   Contrast must be *measured*, not eyeballed: the dark set records 16.8:1, 6.2:1 and a
   deliberate 3.2:1 non-text-only, and the light set owes the same figures. This is
   deterministic arithmetic (WCAG relative luminance) and should be computed and asserted in a
   test, not asserted in prose.
2. **A provider** setting `data-theme` on `<html>`, with the preference persisted and
   `prefers-color-scheme` as the first-visit default.
3. **A pre-hydration inline script** in `index.html` to set the attribute before first paint —
   otherwise every light-mode user gets a dark flash, which Task 17 explicitly forbids.
4. **The toggle** in the top bar, keyboard reachable.
5. **Chart colours** verified in both themes. `--color-status-profit` and `--color-status-loss`
   are currently tuned against a near-black canvas; on white they will need different values,
   and *those two must stay distinguishable to a red-green colour-blind reader in both* —
   which the dark set achieves through hue plus an icon, never colour alone.
6. **Ratchet `no-colour-literals.budget.js`.** The ~150 stray hex literals outside the token
   system (`ExchangeManager` 21, `StrategyMarketplace` 19, `primitives` 19, `AuthPage` 17,
   `RiskSettings` 16, …) are the components that would stay dark even after the generator
   change. The budget already tracks them; it should come down as they convert.

## 6. Why this was not started

Task 17 has no independently shippable slice. A toggle with no light palette does nothing; a
light palette with no toggle is unreachable; the generator change alone has no user-visible
effect and carries the §4 risks. It is one coherent change or none, and it needs a session
with room to compute contrast ratios and verify charts in both themes.

What it does *not* need any more is discovery. The blocker is one line, the dependency count
is measured, and the four classes of consumer that need literals are enumerated.
