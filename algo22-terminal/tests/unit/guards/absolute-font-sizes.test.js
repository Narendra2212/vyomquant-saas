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
 * Task 1.1 was the detection and its self-tests, landed on its own because a
 * silently broken pattern makes the whole guard pass vacuously and the seeded
 * numbers drift into fiction — so the patterns were pinned by example before any
 * number was written down. Task 1.2 then seeded the budget and switched on the
 * ratchet, and open decision O1 resolved to **option B, px-only**; the budget
 * file's header carries that decision, its rationale and its reversibility.
 *
 * So the whole guard is here now:
 *
 *   * the four patterns (§3.2) and `countAbsoluteFontSizes`;
 *   * §3.3's five self-tests, which pin comment blanking, the apostrophe case,
 *     the relative/tokenised exclusions and the neighbouring-property
 *     exclusions;
 *   * §10.3's non-vacuity fixed point: running the four patterns over the three
 *     roots reproduces requirements §1.1's fourteen per-page numbers exactly;
 *   * §3.4's six ratchet assertions over `absolute-font-sizes.budget.js` —
 *     `is well formed`, `names only files that still exist`, `accounts for every
 *     file that still carries an absolute size`, `holds every file at or below
 *     its budget`, `requires progress to be recorded, not banked`, `has an entry
 *     for every file it claims to track, and no strays`.
 *
 * The last two of those, taken together, assert **equality** between every
 * committed number and the tree. That is worth naming, because it extends the
 * fixed point below to all 29 entries rather than only the fourteen pages:
 * requirements §1.4's four landing numbers (`ScreenshotsSection` 12, `Hero` 2,
 * `DownloadSection` 1, `HowItWorks` 1) and §3.5's eleven `components/` numbers
 * are pinned exactly by the budget, in both directions, without needing a second
 * table to compare against. `PAGE_FIXED_POINT` stays at the fourteen numbers
 * requirements §1.1 committed to, which is what it is checkable against.
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
 * keeps it from counting the budget file's own header, which quotes dozens of
 * sizes in prose.
 */

import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, it, expect } from 'vitest';

import { ABSOLUTE_FONT_SIZE_BUDGET } from './absolute-font-sizes.budget.js';
import { TOKEN_LAYER_FILES } from './no-colour-literals.budget.js';
import { SRC, collect, isTestFile, list, relToSrc, stripComments } from './source-scan.js';

const BUDGET_FILE = 'tests/unit/guards/absolute-font-sizes.budget.js';

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

/**
 * The pages this spec has since CLEARED, and what each one measures now.
 *
 * `PAGE_FIXED_POINT` above is requirements §1.1's table and is left exactly as
 * written: it is the tree the spec was read from, and the failure message below
 * says not to edit it to match a measurement. This map is the other half of that
 * instruction — "the change that moved them owes an explanation" — so a page the
 * pass deliberately cleared is recorded here, with the task that cleared it and
 * the number it came down from, rather than by quietly rewriting history.
 *
 * `was` is asserted against `PAGE_FIXED_POINT` below, so the two cannot drift
 * apart: an entry claiming to have cleared a number the spec never recorded, or
 * recording the wrong starting count, fails.
 */
const CLEARED_BY_THIS_SPEC = Object.freeze({
  'pages/StrategyMarketplace.jsx': Object.freeze({ was: 30, now: 0, task: '5.2' }),
  'pages/RiskSettings.jsx': Object.freeze({ was: 16, now: 0, task: '7.2' }),
  'pages/TwoFA.jsx': Object.freeze({ was: 12, now: 0, task: '7.3' }),
  'pages/SecurityLogs.jsx': Object.freeze({ was: 7, now: 0, task: '7.4' }),
});

describe('absolute-font-sizes: the patterns measure the tree they were written for', () => {
  it('reproduces requirements §1.1 per-page counts exactly', () => {
    const expected = {};
    const measured = {};
    for (const relative of Object.keys(PAGE_FIXED_POINT)) {
      const cleared = CLEARED_BY_THIS_SPEC[relative];
      expected[relative] = cleared ? cleared.now : PAGE_FIXED_POINT[relative];
      const file = SCANNED.find((f) => f.relative === relative);
      measured[relative] = file ? file.count : 'NOT SCANNED';
    }

    const drifted = Object.keys(expected)
      .filter((relative) => measured[relative] !== expected[relative])
      .map((relative) => {
        const file = SCANNED.find((f) => f.relative === relative);
        const cleared = CLEARED_BY_THIS_SPEC[relative];
        return `${relative} — ${
          cleared
            ? `task ${cleared.task} took it to ${cleared.now}`
            : `requirements §1.1 says ${PAGE_FIXED_POINT[relative]}`
        }, measured ${measured[relative]}`
          + (file && file.count ? ` (${distribution(file.sizes)})` : '');
      });

    expect(
      drifted,
      `The four patterns no longer reproduce the numbers this spec was written from.\n\n`
        + `Do NOT edit PAGE_FIXED_POINT to match. Either a pattern broke — in which case\n`
        + `the self-tests above should have caught it and one of them needs strengthening —\n`
        + `or the tree moved, in which case the budget seeded from these numbers is stale\n`
        + `and the change that moved them owes an explanation. A page this spec cleared on\n`
        + `purpose goes in CLEARED_BY_THIS_SPEC with its task number, which IS that\n`
        + `explanation; §1.1's own figure stays where it is:\n${list(drifted)}`,
    ).toEqual([]);

    // Non-vacuity for the non-vacuity check: if PAGE_FIXED_POINT were ever
    // emptied, the loop above would pass over nothing.
    expect(Object.keys(PAGE_FIXED_POINT)).toHaveLength(14);
    expect(measured).toEqual(expected);

    // A cleared page's recorded starting count is §1.1's own, and a cleared page
    // has no budget entry left (Requirement 1.5).
    for (const [relative, record] of Object.entries(CLEARED_BY_THIS_SPEC)) {
      expect(record.was, `${relative}: CLEARED_BY_THIS_SPEC disagrees with §1.1`).toBe(
        PAGE_FIXED_POINT[relative],
      );
      if (record.now === 0) {
        expect(
          ABSOLUTE_FONT_SIZE_BUDGET[relative],
          `${relative} measures zero, so its budget entry must be DELETED, not left behind`,
        ).toBeUndefined();
      }
    }
  });
});

// ---------------------------------------------------------------------------
// 3. The budget is a ratchet — design.md §3.4
// ---------------------------------------------------------------------------

/**
 * The colour guard's section 3, one for one, and each of the six catches
 * something the other five do not:
 *
 *   is well formed          a non-integer, a negative, a Windows-slashed key —
 *                           and, unique to this budget, an entry left at `0`
 *   names only files…       an entry left behind by a deleted file
 *   accounts for every…     a new violation, and a reintroduction
 *   holds every file…       a size added to a file that is supposed to shrink
 *   requires progress…      the under-budget direction: sizes removed, budget
 *                           left high, headroom open for them to creep back
 *   has an entry for every… a budget entry naming a file outside SCAN_ROOTS
 *
 * The assertions are **exact, in both directions**, and `no-colour-literals.test.js`'s
 * header states why: under a `<=` assertion a contributor could clear forty
 * sizes, leave the budget high, and the next contributor could put forty back
 * without CI noticing. Progress has to be *recorded* to count, not banked.
 *
 * Requirement 1.5 inverts one rule of the colour budget and it moves which
 * assertion holds a cleared file, so it is worth having in view while reading
 * these six: **an entry that reaches zero is deleted, not left at `0`.** The
 * colour guard keeps `pages/Dashboard.jsx: 0` and `holds every file at or below
 * its budget` fails a reintroduction. Here the entry is gone, and `accounts for
 * every file that still carries an absolute size` fails a reintroduction — as an
 * *unbudgeted* file. Both hold the line; only one of them needs the unbudgeted
 * message to cover two different situations, which is why that message below is
 * longer than the colour guard's.
 */
describe('absolute-font-sizes: the decreasing budget', () => {
  it('is well formed', () => {
    for (const [relative, budget] of Object.entries(ABSOLUTE_FONT_SIZE_BUDGET)) {
      expect(Number.isInteger(budget), `${relative}: ${budget} is not an integer`).toBe(true);
      expect(relative, `${relative} must be relative to src/ with forward slashes`).toMatch(
        /^[\w.-]+(?:\/[\w.-]+)*$/,
      );
      // Requirement 1.5's inversion, enforced rather than described. `> 0`, not
      // `>= 0` — this is the one clause where this budget deliberately differs
      // from the colour guard's, and without it the inversion would be advisory:
      // an entry left at `0` is invisible to the other five. `accounts for every
      // file…` only fires on files measuring more than zero; `requires progress
      // to be recorded, not banked` compares `0 < 0` and passes. So a cleared
      // file whose entry was left behind would sit there holding headroom that
      // nothing reports, which is the exact failure the inversion exists to
      // remove.
      expect(
        budget,
        `${relative}: ${budget}. An entry that reaches zero is DELETED, not set to 0 `
          + `(Requirement 1.5) — a file with no entry is held at zero by default, and\n`
          + `\`accounts for every file that still carries an absolute size\` is what fails if a\n`
          + `size comes back into it. Remove this line from ${BUDGET_FILE}.`,
      ).toBeGreaterThan(0);
    }
  });

  it('names only files that still exist', () => {
    const gone = Object.keys(ABSOLUTE_FONT_SIZE_BUDGET).filter(
      (relative) => !existsSync(path.join(SRC, relative)),
    );
    expect(
      gone,
      `These budget entries name files that are no longer in src/. A deleted file takes its\n`
        + `entry with it — remove them from ${BUDGET_FILE}. Task 9.2 deletes\n`
        + `pages/Landing.jsx and its 41, and task 9.1 deletes seven landing components:\n${list(gone)}`,
    ).toEqual([]);
  });

  it('accounts for every file that still carries an absolute size', () => {
    const unbudgeted = SCANNED.filter(
      (f) => f.count > 0 && !(f.relative in ABSOLUTE_FONT_SIZE_BUDGET),
    ).map((f) => `${f.relative} — ${f.count} absolute size(s) (${distribution(f.sizes)})`);

    expect(
      unbudgeted,
      `Absolute font sizes in files with no budget entry.\n\n`
        + `A file with no entry is held at zero — see Requirement 1.5. If this file was\n`
        + `previously cleared, a size has come back: express it as a Type_Scale step from\n`
        + `src/styles/tokens.css. Re-adding a budget entry is not the remedy.\n\n`
        + `If this file is new to the tree and genuinely carries debt, seed an entry with the\n`
        + `change that clears it.\n${list(unbudgeted)}`,
    ).toEqual([]);
  });

  it('holds every file at or below its budget', () => {
    const over = SCANNED.filter(
      (f) =>
        f.relative in ABSOLUTE_FONT_SIZE_BUDGET
        && f.count > ABSOLUTE_FONT_SIZE_BUDGET[f.relative],
    ).map(
      (f) =>
        `${f.relative} — budget ${ABSOLUTE_FONT_SIZE_BUDGET[f.relative]}, actual ${f.count} `
        + `(+${f.count - ABSOLUTE_FONT_SIZE_BUDGET[f.relative]}) (${distribution(f.sizes)})`,
    );

    expect(
      over,
      `Absolute font sizes were added to files that are supposed to be shrinking.\n\n`
        + `Use a Type_Scale step from src/styles/tokens.css — one of the seven --text-*\n`
        + `values, which are declared in rem and therefore scale with the reader's browser\n`
        + `font-size preference (Requirement 2.1). A device-pixel value does not.\n`
        + `Raising a budget in ${BUDGET_FILE} reverses Requirement 1.1 and needs a reason in\n`
        + `the PR.\n${list(over)}`,
    ).toEqual([]);
  });

  it('requires progress to be recorded, not banked', () => {
    const under = SCANNED.filter(
      (f) =>
        f.relative in ABSOLUTE_FONT_SIZE_BUDGET
        && f.count < ABSOLUTE_FONT_SIZE_BUDGET[f.relative],
    ).map((f) =>
      f.count === 0
        ? `${f.relative} — cleared. DELETE the entry (currently `
          + `${ABSOLUTE_FONT_SIZE_BUDGET[f.relative]}); do not set it to 0`
        : `${f.relative} — lower the committed budget from `
          + `${ABSOLUTE_FONT_SIZE_BUDGET[f.relative]} to ${f.count} (${distribution(f.sizes)})`,
    );

    expect(
      under,
      `These files now declare fewer absolute font sizes than their committed budget.\n`
        + `Good — but the budget has to come down with them, in this commit, or the headroom\n`
        + `stays open for the sizes to creep back in unnoticed. Edit ${BUDGET_FILE}.\n\n`
        + `A file that reached ZERO has its entry deleted rather than lowered to 0\n`
        + `(Requirement 1.5) — the lines above say which case each file is:\n${list(under)}`,
    ).toEqual([]);
  });

  it('has an entry for every file it claims to track, and no strays', () => {
    const scannedNames = new Set(SCANNED.map((f) => f.relative));
    const strays = Object.keys(ABSOLUTE_FONT_SIZE_BUDGET).filter(
      (relative) => !scannedNames.has(relative),
    );
    expect(
      strays,
      `These budget entries name files that exist but are outside this guard's scan\n`
        + `(${SCAN_ROOTS.map((root) => `src/${root}`).join(', ')}, excluding tests and the token\n`
        + `layer). Remove them from ${BUDGET_FILE} or widen SCAN_ROOTS deliberately:\n`
        + `${list(strays)}`,
    ).toEqual([]);
  });
});
