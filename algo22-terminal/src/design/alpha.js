/**
 * src/design/alpha.js — translucency over a theme-aware colour.
 *
 * WHY THIS EXISTS
 * ---------------------------------------------------------------------------
 * Until light mode, 54 call sites made a colour translucent by concatenating two
 * hex digits onto a token:
 *
 *     background: `${token.brand.base}15`      // #00D4FF + "15" → #00D4FF15
 *
 * That works only because `src/design/tokens.js` mirrored each token as a
 * RESOLVED LITERAL. It cannot survive a runtime theme switch: the mirror now
 * emits `var(--color-brand)` so the browser resolves the value per theme, and
 * `var(--color-brand)15` is not a colour — it is invalid CSS that the browser
 * drops silently, leaving a transparent background and no error.
 *
 * `color-mix()` is the replacement. It composes at paint time, so it follows
 * whichever theme is active, and it accepts a `var()` reference as its input.
 *
 *     background: alpha(token.brand.base, 8)
 *     → color-mix(in srgb, var(--color-brand) 8%, transparent)
 *
 * WHY NOT A TOKEN PER PAIR
 * ---------------------------------------------------------------------------
 * The alternative was declaring a token for every (colour, alpha) combination.
 * Measured before deciding: 13 distinct alpha values across 6 base colours, so
 * up to 78 new names in `tokens.css`, each needing a light counterpart. It also
 * cannot express the three dynamic sites, where the base colour is chosen at
 * runtime:
 *
 *     alpha(wsConnected ? token.status.profit.fg : token.status.loss.fg, 25)
 *     alpha(SEVERITY_COLORS[n.severity] || token.brand.base, 9)
 *     alpha(toneColour(tone) || token.content.secondary, 33)
 *
 * A function takes those without a special case. 78 tokens could not.
 *
 * ON THE PERCENTAGES
 * ---------------------------------------------------------------------------
 * The old two-digit suffixes were 8-bit alpha; these are percentages, converted
 * as `round(hex / 255 * 100)`. The largest rounding error is under half a
 * percent — about one step in 255 — which is not a perceptible change to a
 * translucent wash. Stated because it IS a change, however small, rather than
 * left for a reader to discover.
 *
 * BROWSER SUPPORT
 * ---------------------------------------------------------------------------
 * `color-mix()` is in Chrome 111+, Safari 16.2+ and Firefox 113+ (all from
 * 2023). The desktop build ships its own WebView through Tauri, and the web app
 * targets current browsers, so there is no engine in scope without it. There is
 * deliberately NO fallback: a silent fallback to an opaque colour would make a
 * wash look like a solid fill and nobody would notice. If support ever becomes
 * a question, the honest fix is a build-time check, not a runtime guess.
 */

/**
 * A translucent version of `color`.
 *
 * @param {string} color    Any CSS colour. Normally a token, which is a
 *                          `var(--color-…)` reference.
 * @param {number} percent  Opacity, 0–100.
 * @returns {string}        A `color-mix()` expression.
 */
export const alpha = (color, percent) =>
  `color-mix(in srgb, ${color} ${percent}%, transparent)`;

export default alpha;
