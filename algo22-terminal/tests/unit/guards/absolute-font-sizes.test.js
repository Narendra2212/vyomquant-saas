/**
 * `absolute-font-sizes` — retail-ui-simplification task 1.1.
 * Requirements 1.2, 1.3, 18.1. design.md §3.1, §3.2, §3.3, §10.3.
 *
 * ===========================================================================
 * WHAT THIS GUARD IS FOR
 * ===========================================================================
 * Requirement 1.1 puts every font size in one source — the seven `--text-*`
 * steps in `src/styles/tokens.css`. Requirement 1.2 records what the tree
 * actually holds: 451 absolute pixel sizes across 14 files, in four syntaxes.
 *
 * Requirement 1.3 asks for a per-file budget "constructed the same way
 * `no-colour-literals.budget.js` is and sharing its `source-scan.js` helper",
 * and the reason to mirror rather than improve is requirements §1.2's own
 * diagnosis: colour got a ratchet and reached zero on eleven pages; font size
 * got a convention and reached 451. The mechanism that worked is the one to
 * copy.
 *
 * ===========================================================================
 * WHAT IS IN THIS FILE, AND WHAT IS NOT YET
 * ===========================================================================
 * Task 1.1 is the detection and its self-tests. It is deliberately separate
 * from the seed, because a silently broken pattern makes the whole guard pass
 * vacuously and the seeded numbers drift into fiction — so the patterns are
 * pinned by example before any number is written down.
 *
 * Present here:
 *
 *   * the four patterns (§3.2) and `countAbsoluteFontSizes`;
 *   * §3.3's five self-tests, which pin comment blanking, the apostrophe case,
 *     the relative/tokenised exclusions and the neighbouring-property
 *     exclusions;
 *   * §10.3's non-vacuity fixed point: running the four patterns over the three
 *     roots reproduces requirements §1.1's fourteen per-page numbers exactly.
 *
 * Not here, and deliberately: `absolute-font-sizes.budget.js` and the six
 * ratchet assertions of §3.4 (`is well formed`, `names only files that still
 * exist`, `accounts for every file that still carries an absolute size`, `holds
 * every file at or below its budget`, `requires progress to be recorded, not
 * banked`, `has an entry for every file it claims to track, and no strays`).
 * Those are task 1.2, which must land alone and is blocked on open decision O1
 * — the seed's *shape* depends on the answer (§7.6), so seeding it now would
 * mean writing a number down twice.
 *
 * ===========================================================================
 * WHY STRINGS ARE NOT MASKED
 * ===========================================================================
 * `source-scan.js` exports `maskStrings` and `codeOnly`, and this guard uses
 * neither. Its own docblock states the rule: mask "when the thing being
 * detected is a *code construct* and a mention of it inside a string literal …
 * is not an instance of it", and do not mask "when the thing being detected
 * lives inside strings by nature". Both halves apply here at once —
 * `fontSize: 11` lives in code, `text-[10px]` lives inside a `className`
 * string. Masking would blind the guard to the entire Tailwind syntax: all 30
 * of `StrategyMarketplace.jsx`'s and all 16 on the landing surface. So strings
 * stay intact, exactly as the colour guard leaves `"#ef4444"` intact.
 *
 * ===========================================================================
 * WHY COMMENT BLANKING IS NOT OPTIONAL HERE
 * ===========================================================================
 * `no-colour-literals` strips comments so that a header documenting a retired
 * `#FFB74D` does not score a literal. This guard needs it for a stronger
 * reason: Requirement 2.4 requires every migrated call site to carry a comment
 * naming the size it came from (`// fontSize: 9 → --text-body (sentence)`). The
 * migration's audit trail is 451 lines of prose about the construct this guard
 * forbids. Without blanking, a file cleared correctly and documented exactly as
 * Requirement 2.4 demands would measure *the same count it started with*, and
 * the only way to pass would be to delete the record. A guard that punishes
 * writing down what you did is a guard that gets the record deleted.
 *
 * `components/ds/SectionHeader.jsx` already proves it in the tree: it measures
 * 2 before stripping and 0 after, because `:17`'s docblock records the
 * `fontSize: 13` / `fontSize: 10` values `PanelTitle` used to hardcode. It gets
 * no budget entry. `ds/Chart.jsx`'s docblocks (`:291`, `:294`, `:719`, `:726`)
 * discuss tick sizes in prose and are the same case.
 *
 * `stripComments` is deliberately not a tokeniser, and its header records the
 * two failures that shaped it — an earlier tokeniser read the apostrophe in JSX
 * prose (`Don't`) as a string opener and lost 33 of `StrategyBuilder.jsx`'s 148
 * `C.` references, seeding a budget 22% low; and an earlier `[...source]` spread
 * collapsed surrogate pairs so every blank in `pages/Strategies.jsx` landed up
 * to 18 characters left of its comment. `is not confused by an apostrophe in
 * JSX text` below is the first of those pinned here.
 *
 * ===========================================================================
 * SCOPE
 * ===========================================================================
 * `SCAN_ROOTS` and `SCANNED_EXTENSIONS` are the colour guard's, per Requirement
 * 1.3: `['pages', 'components', 'lib']` and
 * `['.js', '.jsx', '.ts', '.tsx', '.css']`. `TOKEN_LAYER_FILES` is imported
 * from `no-colour-literals.budget.js` rather than re-declared — `styles/
 * tokens.css` must be out of scope here for the same reason it is out of scope
 * there: a file whose job is to declare font sizes cannot be policed by a rule
 * that forbids them. `isTestFile` keeps the guard from arguing with tests
 * (`expect(style.fontSize).toBe(11)` is a test doing its job) and incidentally
 * keeps it from counting task 1.2's budget file header.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, it, expect } from 'vitest';

import { TOKEN_LAYER_FILES } from './no-colour-literals.budget.js';
import { SRC, collect, isTestFile, list, relToSrc, stripComments } from './source-scan.js';

/**
 * Requirement 1.3: the colour guard's roots, unchanged. Narrowing to `pages`
 * plus `components/landing` so the scope matched Requirement 1.4's 18-entry
 * seed was considered and rejected in §3.5 — it would leave `ds/Chart.jsx:722`
 * permanently invisible, and that one declaration sets the axis tick size on
 * every chart on all eleven migrated pages.
 */
const SCAN_ROOTS = Object.freeze(['pages', 'components', 'lib']);

const SCANNED_EXTENSIONS = Object.freeze(['.js', '.jsx', '.ts', '.tsx', '.css']);

// ---------------------------------------------------------------------------
// Counting — design.md §3.2
// ---------------------------------------------------------------------------

/** `fontSize: 11`. The lookbehind is the guard's own idiom — see FUNCTIONAL_COLOUR. */
const FONT_SIZE_NUMERIC = /(?<![\w$])fontSize\s*:\s*(\d+)(?![\d.])/g;

/** `fontSize: '14px'` / `fontSize: "14px"`. The `px` is what excludes rem. */
const FONT_SIZE_QUOTED = /(?<![\w$])fontSize\s*:\s*['"](\d+(?:\.\d+)?)px['"]/g;

/** `text-[10px]`. Tailwind arbitrary value. */
const TAILWIND_ARBITRARY = /(?<![\w-])text-\[(\d+(?:\.\d+)?)px\]/g;

/** `fontSize={9}`. The fourth syntax — recharts axis props, PaperTrading.jsx only. */
const FONT_SIZE_PROP = /(?<![\w$])fontSize\s*=\s*\{\s*(\d+)(?![\d.])\s*\}/g;

/**
 * All four, in declaration order.
 *
 * The `(?<![\w$])` lookbehind is copied from `no-colour-literals.test.js`'s
 * `FUNCTIONAL_COLOUR` and from `source-scan.js`'s `STRING_SPAN`. It stops
 * `baseFontSize:`, `minFontSize:` and `tickFontSize:` counting as `fontSize:`.
 * `\b` was the alternative and lost: it does not fire between `e` and `F` in
 * `baseFontSize`, so it looks correct and is not.
 *
 * The `(?![\d.])` lookahead stops `fontSize: 10.5` counting as `10`. It changes
 * no number today — no fractional px exists in the tree — and it means a
 * fractional value reports as *uncounted* rather than as a wrong count, which
 * is a miss task 1.2's `accounts for every file` assertion catches rather than a
 * silent mis-parse.
 */
const PATTERNS = Object.freeze([
  FONT_SIZE_NUMERIC,
  FONT_SIZE_QUOTED,
  TAILWIND_ARBITRARY,
  FONT_SIZE_PROP,
]);

/**
 * The absolute pixel sizes a source file declares, as the digits themselves.
 *
 * The colour guard counts; this one captures. §2's migration procedure takes the
 * number as evidence about the role a size is playing, so a failure message that
 * can say `PaperTrading.jsx — 51 absolute sizes (9×31, 11×10, 10×8, 8×1, 16×1)`
 * is a message that shortens the work.
 *
 * Comments are blanked first; strings are not — see the header for both.
 */
export function absoluteFontSizes(source, { lineComments = true } = {}) {
  const code = stripComments(source, { lineComments });
  return PATTERNS.flatMap((pattern) => [...code.matchAll(pattern)].map((m) => m[1]));
}

/** How many absolute pixel sizes a source file declares. */
export function countAbsoluteFontSizes(source, options) {
  return absoluteFontSizes(source, options).length;
}

/** `9×31, 11×10, 10×8, 8×1, 16×1` — commonest first, then smallest size first. */
export function distribution(sizes) {
  const tally = new Map();
  for (const size of sizes) tally.set(size, (tally.get(size) ?? 0) + 1);
  return [...tally.entries()]
    .sort((a, b) => b[1] - a[1] || Number(a[0]) - Number(b[0]))
    .map(([size, n]) => `${size}\u00d7${n}`)
    .join(', ');
}

// ---------------------------------------------------------------------------
// Scanning
// ---------------------------------------------------------------------------

/** `[{ relative, count, sizes }]` for every file this guard governs. */
function scan() {
  const files = [];
  for (const root of SCAN_ROOTS) {
    for (const full of collect(path.join(SRC, root), SCANNED_EXTENSIONS)) {
      const relative = relToSrc(full);
      if (isTestFile(relative) || TOKEN_LAYER_FILES.includes(relative)) continue;
      // `//` is not a comment in CSS, and `text-[10px]` can appear in an
      // `@apply`. Same option, same reason, same line as the colour guard's.
      const sizes = absoluteFontSizes(readFileSync(full, 'utf8'), {
        lineComments: path.extname(full) !== '.css',
      });
      files.push({ relative, count: sizes.length, sizes });
    }
  }
  return files.sort((a, b) => a.relative.localeCompare(b.relative));
}

const SCANNED = scan();

// ---------------------------------------------------------------------------
// 1. The counting method itself — design.md §3.3
// ---------------------------------------------------------------------------

describe('absolute-font-sizes: the counting method', () => {
  it('ignores a size named in a comment', () => {
    // Requirement 2.4 requires every migrated call site to carry exactly this
    // kind of note. Without blanking, documenting the migration would preserve
    // the count and the only way to pass would be to delete the record.
    expect(countAbsoluteFontSizes('/* was fontSize: 9 — now --text-body */')).toBe(0);
    expect(countAbsoluteFontSizes('const a = 1; // text-[10px] retired')).toBe(0);
    expect(countAbsoluteFontSizes(['/**', ' * fontSize: 11 and text-[9px]', ' */'].join('\n'))).toBe(0);
  });

  it('still counts a size on a line that also carries a comment', () => {
    expect(countAbsoluteFontSizes('fontSize: 11, // table body')).toBe(1);
  });

  it('is not confused by an apostrophe in JSX text', () => {
    // The failure mode that killed the tokeniser `stripComments` replaced: one
    // apostrophe swallowed the rest of StrategyBuilder.jsx and cost 33 of 148
    // references, seeding a budget 22% low.
    expect(countAbsoluteFontSizes("<p>Don't stop</p><b style={{ fontSize: 9 }} />")).toBe(1);
  });

  it('does not count a relative or tokenised size', () => {
    // `'1.25rem'` is PaperTrading.jsx:2355 — off-scale, and §3.6's first blind
    // spot rather than a match. It scales with the reader; that is what the
    // literal `px` in the two quoted patterns is drawing the line at.
    expect(countAbsoluteFontSizes("fontSize: '1.25rem'")).toBe(0);
    expect(countAbsoluteFontSizes("className='text-[0.625rem] text-micro'")).toBe(0);
    expect(countAbsoluteFontSizes('fontSize: token.text.body')).toBe(0);
    expect(countAbsoluteFontSizes("fontSize: 'var(--text-body)'")).toBe(0);
  });

  it('does not count a neighbouring numeric style property', () => {
    // The `fontSize` anchor is what excludes all of these. `size={9}` is the one
    // worth naming: it is a device-pixel number inside the very chip whose text
    // size *is* counted (PaperTrading.jsx:572).
    expect(countAbsoluteFontSizes('lineHeight: 1.2, letterSpacing: 2, borderRadius: 12')).toBe(0);
    expect(countAbsoluteFontSizes('<FlaskConical size={9} strokeWidth={2.5} />')).toBe(0);
    // The lookbehind, not `\b`, is what makes this one pass.
    expect(countAbsoluteFontSizes('const baseFontSize = 11;')).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// 2. Non-vacuity — design.md §10.3
// ---------------------------------------------------------------------------

/**
 * Requirements §1.1's per-page table, measured. This is the guard's fixed point
 * and it is an assertion rather than a claim for the reason §10.3 gives: every
 * other guard under `tests/unit/guards/` has one, `dead-tailwind.test.js` is the
 * one that cannot, and a guard that measured nothing would pass vacuously while
 * its budget numbers drifted into fiction.
 *
 * `pages/PaperTrading.jsx` is 51 rather than Requirement 1.4's 43: the
 * difference is the eight `fontSize={9}` recharts axis props that `FONT_SIZE_PROP`
 * exists for. §3.5 records that as a correction to an approved document rather
 * than applying it silently.
 *
 * These fourteen are not the whole scan — eleven files under `components/` carry
 * sizes with no entry in Requirement 1.4's seed, and task 1.2 owns them. They
 * are the fourteen requirements §1.1 committed to a number for, so they are the
 * fourteen this reproduction is checkable against.
 */
const PAGE_FIXED_POINT = Object.freeze({
  'pages/Profile.jsx': 79,
  'pages/StrategyDetail.jsx': 61,
  'pages/ExchangeManager.jsx': 52,
  'pages/PaperTrading.jsx': 51,
  'pages/Landing.jsx': 41,
  'pages/Billing.jsx': 39,
  'pages/StrategyMarketplace.jsx': 30,
  'pages/AuthPage.jsx': 29,
  'pages/Wizard.jsx': 24,
  'pages/RiskSettings.jsx': 16,
  'pages/LegalPage.jsx': 15,
  'pages/TwoFA.jsx': 12,
  'pages/SecurityLogs.jsx': 7,
  'pages/UpdatePasswordPage.jsx': 3,
});

describe('absolute-font-sizes: the patterns measure the tree they were written for', () => {
  it('reproduces requirements §1.1 per-page counts exactly', () => {
    const measured = {};
    for (const relative of Object.keys(PAGE_FIXED_POINT)) {
      const file = SCANNED.find((f) => f.relative === relative);
      measured[relative] = file ? file.count : 'NOT SCANNED';
    }

    const drifted = Object.keys(PAGE_FIXED_POINT)
      .filter((relative) => measured[relative] !== PAGE_FIXED_POINT[relative])
      .map((relative) => {
        const file = SCANNED.find((f) => f.relative === relative);
        return `${relative} — requirements §1.1 says ${PAGE_FIXED_POINT[relative]}, `
          + `measured ${measured[relative]}`
          + (file && file.count ? ` (${distribution(file.sizes)})` : '');
      });

    expect(
      drifted,
      `The four patterns no longer reproduce the numbers this spec was written from.\n\n`
        + `Do NOT edit PAGE_FIXED_POINT to match. Either a pattern broke — in which case\n`
        + `the self-tests above should have caught it and one of them needs strengthening —\n`
        + `or the tree moved, in which case the budget seeded from these numbers is stale\n`
        + `and the change that moved them owes an explanation:\n${list(drifted)}`,
    ).toEqual([]);

    // Non-vacuity for the non-vacuity check: if PAGE_FIXED_POINT were ever
    // emptied, the loop above would pass over nothing.
    expect(Object.keys(PAGE_FIXED_POINT)).toHaveLength(14);
    expect(measured).toEqual(PAGE_FIXED_POINT);
  });
});
