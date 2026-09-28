/**
 * tests/unit/guards/css-layer-order.test.js
 *
 * ONE RULE: a global reset in `src/index.css` must live inside `@layer base`.
 *
 * ═══ WHY THIS GUARD EXISTS ═══
 *
 * `index.css` declared its global reset at the top level, outside any cascade layer:
 *
 *     * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: … }
 *
 * Cascade layers rank BELOW unlayered CSS, always, whatever the specificity. Tailwind v4 emits
 * every utility into `@layer utilities`, so that one rule — specificity (0,0,0) — beat `.py-20`,
 * `.p-7`, `.mt-8`, `.mx-auto` and every other margin or padding utility in the bundle.
 *
 * Measured on the production build before the fix: the reset compiled at byte offset 67210,
 * outside all five layers, while `@layer utilities` opened at 10567.
 *
 * WHAT IT LOOKED LIKE, because the symptom did not look like a stylesheet bug:
 *
 *   * every `<section>` computed `padding-top: 0` and `padding-bottom: 0`, so eleven sections
 *     butted against each other with no separation;
 *   * `.section-inner` kept its `max-width: 80rem` but computed `margin-inline: 0px` instead of
 *     `auto`, so every section pinned hard to the left edge of the viewport;
 *   * the hero's `pt-32` collapsed and the `<h1>` rendered underneath the fixed navbar;
 *   * `scrollbar-hide`'s `scrollbar-width: none` was overridden by the second unlayered `*` rule
 *     at the foot of the file, so that utility could never take effect anywhere.
 *
 * Colour, border, `max-width`, `display` and `gap` were all untouched — none of them is a margin
 * or a padding — which is precisely why it read as a badly designed page rather than as a broken
 * one. `gap` surviving is the detail that makes it diagnosable: grids kept their column gaps
 * while the cards inside them had no internal padding at all.
 *
 * It reached every route, not just the landing page. It arrived in `cf6318c`, which moved the
 * reset out of the inline `<style>` that `AppShell` rendered — correct in itself, since a global
 * reset has no business being re-evaluated on every render of a component. What the move did not
 * carry was the scoping: as an inline `<style>` the reset was only in the DOM while `AppShell`
 * was mounted, which is `/app/*`. Hoisting it into `index.css` made it global.
 *
 * ═══ WHY IT IS A SOURCE SCAN AND NOT A BUILD SCAN ═══
 *
 * Reading `dist/` would be the more direct measurement — it is where the offsets above were
 * taken — but it would make this guard depend on a fresh `npm run build`, the way
 * `dead-tailwind`'s `needsBuild` cases do, and skip silently when `dist/` is stale or absent.
 * The decision this protects is a source decision: whether an author wrapped a reset in a layer.
 * That is legible in `index.css` and needs no toolchain.
 *
 * ═══ SCOPE ═══
 *
 * `@utility` blocks are deliberately NOT required to be layered: Tailwind consumes that directive
 * and places the output in `@layer utilities` itself. Nor is `@import`, `@source`, `@theme` or
 * `@media`. The rule is narrow on purpose — a bare selector at the top level of this file that
 * sets `margin` or `padding` — because that is the exact shape of the defect.
 */

import { readFileSync } from 'node:fs'
import path from 'node:path'

import { describe, it, expect } from 'vitest'

import { SRC } from './source-scan.js'

const INDEX_CSS = 'index.css'
const raw = readFileSync(path.join(SRC, INDEX_CSS), 'utf8')

/**
 * Comments blanked, LENGTH PRESERVED, so every offset below indexes the real file.
 *
 * This runs before anything else reads the file, and that ordering is load-bearing. The header
 * comments in `index.css` quote CSS at length — including the very `* { margin: 0; padding: 0 }`
 * rule this guard is about, and the phrase `@layer base { … }` — so their braces are unbalanced
 * with respect to the surrounding code. An earlier version of this file counted braces on the raw
 * source and the layer spans came out wrong: the `*` reset measured as unlayered while sitting
 * inside `@layer base`, and the guard failed on correct source. Documenting a defect must not
 * look like the defect.
 */
const blankComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, (m) => ' '.repeat(m.length))

const source = blankComments(raw)

/**
 * The spans of `source` that sit inside a `@layer …{ … }` block, as [start, end) pairs.
 *
 * Brace counting rather than a regex, because a layer body contains nested blocks — `@media`
 * inside `@utility`, `&:hover` inside a rule — and a non-greedy `\{[^}]*\}` would close the layer
 * at the first inner `}` and report layered rules as unlayered.
 */
function layerSpans(css) {
  const spans = []
  const opener = /@layer\s+[\w\s,-]*\{/g
  let match = opener.exec(css)
  while (match !== null) {
    let depth = 0
    let i = match.index + match[0].length - 1
    for (; i < css.length; i += 1) {
      if (css[i] === '{') depth += 1
      else if (css[i] === '}') {
        depth -= 1
        if (depth === 0) break
      }
    }
    spans.push([match.index, i])
    opener.lastIndex = i
    match = opener.exec(css)
  }
  return spans
}

const SPANS = layerSpans(source)
const insideALayer = (index) => SPANS.some(([start, end]) => index > start && index < end)

/** A declaration list that sets `margin` or `padding` (not `margin-*`/`padding-*` longhands). */
const SETS_SPACING = /(?:^|[;{\s])(?:margin|padding)\s*:/

/**
 * Every rule whose body sets `margin` or `padding` on a bare selector — no `@` prefix, so
 * `@utility`, `@media` and `@layer` headers are excluded — with whether it sits inside a layer.
 *
 * A LINEAR SCAN, NOT A REGEX, and that is a correction rather than a style preference. This was
 * `/(^|[{};])\s*([^{}@;]+?)\s*\{([^{}]*)\}/g`, and the lazy `+?` between two `\s*` quantifiers
 * backtracked catastrophically over a 9 KB stylesheet: each of the two assertions that call it
 * took ~24 s and both tripped vitest's 15 s timeout. They failed as TIMEOUTS, not as assertion
 * errors, which is worth naming — the guard looked like it had found a defect in `index.css`
 * when it had merely failed to finish reading it.
 *
 * The scan walks the source once. At each `{` it takes the text back to the previous `{`, `}` or
 * `;` as the selector and the matching block as the body, then descends into the block so that
 * rules nested inside `@layer base { … }` are reached — which is the whole point, since that is
 * where a correctly-layered reset lives.
 */
function spacingResets(css) {
  const code = blankComments(css)
  const found = []
  let selStart = 0

  for (let i = 0; i < code.length; i += 1) {
    const ch = code[i]

    if (ch === '}' || ch === ';') {
      selStart = i + 1
      continue
    }
    if (ch !== '{') continue

    // Matching close brace for this block.
    let depth = 1
    let j = i + 1
    for (; j < code.length && depth > 0; j += 1) {
      if (code[j] === '{') depth += 1
      else if (code[j] === '}') depth -= 1
    }

    const body = code.slice(i + 1, j - 1)
    const selector = code.slice(selStart, i).trim()

    // A leaf rule only: a body containing `{` is a container (`@layer`, `@media`, `@utility`),
    // and its own declarations are reached on the next pass through the loop.
    if (!body.includes('{') && selector && !selector.startsWith('@') && SETS_SPACING.test(body)) {
      found.push({ selector, body: body.trim(), index: i, layered: insideALayer(i) })
    }

    // Descend rather than skip, so nested rules are visited.
    selStart = i + 1
  }

  return found
}

describe('css-layer-order: index.css resets cannot outrank Tailwind utilities', () => {
  it('declares the layers it means to use', () => {
    // Non-vacuity. If `@layer base` were removed entirely, every assertion below would pass
    // over an empty set of spans and report nothing.
    expect(SPANS.length, 'index.css declares no @layer block at all').toBeGreaterThan(0)
    expect(source).toMatch(/@layer\s+base\s*\{/)
  })

  it('puts every margin/padding reset inside a layer', () => {
    const unlayered = spacingResets(source)
      .filter((r) => !r.layered)
      .map((r) => `  ${r.selector} { ${r.body.replace(/\s+/g, ' ').slice(0, 90)} }`)

    expect(
      unlayered,
      'These rules set margin or padding OUTSIDE any @layer in src/index.css.\n\n'
        + 'Unlayered CSS outranks every cascade layer regardless of specificity, and Tailwind v4\n'
        + 'emits its utilities into `@layer utilities`. A top-level `* { margin: 0; padding: 0 }`\n'
        + 'therefore silently defeats every p-*, m-*, px-*, py-* and mx-auto utility in the app:\n'
        + 'sections lose their padding, `.section-inner` loses `margin-inline: auto` and pins to\n'
        + 'the left edge, and the hero renders under the fixed navbar. Colour, border, max-width\n'
        + 'and gap keep working, so it reads as a design problem rather than a stylesheet one.\n\n'
        + `Wrap them in \`@layer base { … }\` in src/${INDEX_CSS}:\n${unlayered.join('\n')}`,
    ).toEqual([])
  })

  it('keeps the universal reset layered, by name', () => {
    // The specific rule that caused it, pinned so a future edit cannot lift just this one back
    // out while leaving the general assertion above satisfied by its neighbours.
    const resets = spacingResets(source).filter((r) => r.selector === '*')
    expect(resets.length, 'the `*` reset is gone from index.css entirely').toBeGreaterThan(0)
    for (const reset of resets) {
      expect(reset.layered, `\`* { ${reset.body.slice(0, 60)} }\` is not inside a layer`).toBe(true)
    }
  })

  it('keeps the scrollbar reset layered, so scrollbar-hide can win', () => {
    // The second half of the same defect. `* { scrollbar-width: thin }` unlayered outranked the
    // `scrollbar-hide` utility's `scrollbar-width: none`, so that utility was inert app-wide.
    const i = source.indexOf('scrollbar-width: thin')
    expect(i, 'the scrollbar reset is gone from index.css').toBeGreaterThan(-1)
    expect(insideALayer(i), '`* { scrollbar-width: thin }` is not inside a layer').toBe(true)
  })

  it('leaves @utility blocks alone, which Tailwind layers itself', () => {
    // Guards against over-correction: `@utility` is a Tailwind directive whose output the
    // compiler places in `@layer utilities`. Wrapping one in `@layer base` would demote the
    // landing page's own compositions below the utilities that are supposed to override them.
    const utilities = [...source.matchAll(/@utility\s+([\w-]+)\s*\{/g)].map((m) => ({
      name: m[1],
      layered: insideALayer(m.index),
    }))

    expect(utilities.length, 'index.css declares no @utility blocks').toBeGreaterThan(0)
    expect(
      utilities.filter((u) => u.layered).map((u) => u.name),
      '@utility blocks must stay at the top level — Tailwind assigns their layer.',
    ).toEqual([])
  })

  it('detects the defect on a planted example, and clears a layered one', () => {
    // The guard's own teeth, so "no unlayered resets found" is a measurement rather than a
    // property of a scan that stopped working.
    const bad = spacingResets('* { margin: 0; padding: 0 }')
    expect(bad).toHaveLength(1)
    expect(bad[0].layered).toBe(false)

    const good = layerSpans('@layer base { * { margin: 0; padding: 0 } }')
    expect(good).toHaveLength(1)
    const [start, end] = good[0]
    expect(end).toBeGreaterThan(start)
  })
})
