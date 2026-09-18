/**
 * `no-colour-literals` — vyomquant-ui-redesign task 1.10.
 * Requirements 1.1, 1.3. design.md §1.1 G5, §14.4, §15.1.
 *
 * ===========================================================================
 * WHAT THIS GUARD IS FOR
 * ===========================================================================
 * Requirement 1.1 puts every colour in one source. design.md §14.4 then admits
 * that Requirement 1.3 cannot be fully true until the end of M9, and buys that
 * admission with a promise: "Every step in between strictly increases primitive
 * coverage; the CI check in §15 asserts monotonic progress (the count of raw
 * <table>/hex literals in in-scope pages may only decrease)."
 *
 * This file is that check. It measures the hex / `rgb()` / `hsl()` literals in
 * `src/pages/**` and `src/components/**` and compares each file against a
 * checked-in budget, and it fails in BOTH directions:
 *
 *   over budget  — a new literal entered the tree. The obvious half.
 *   under budget — literals were removed and the budget was left where it was.
 *
 * The second half is why this is a ratchet rather than a ceiling. Under a `<=`
 * assertion a contributor could clear forty literals from Dashboard.jsx, leave
 * the budget at 315, and the next contributor could put forty back without CI
 * noticing. Progress has to be *recorded* to count, not banked. That is the
 * whole mechanical content of "monotonic".
 *
 * ===========================================================================
 * AND WHY A RATCHET IS NOT ENOUGH — TASK 27.3
 * ===========================================================================
 * A ratchet has a resting position. `pages/ExchangeManager.jsx: 128` can sit at
 * 128 forever and fail nothing, which is right: no task in this spec is
 * scheduled to lower it. But the same leniency covered the pages the spec DID
 * promise to clear, and Requirement 1.3 is a statement about that enumerated
 * set, not about the tree in general. Nothing in section 3 below knows which
 * files are which.
 *
 * So task 27.3 split the budget into `IN_SCOPE_COLOUR_LITERAL_BUDGET` — the
 * eleven pages with an M6-M9 page task, plus the shared surfaces those tasks
 * built or cleared — and `OUT_OF_SCOPE_COLOUR_LITERAL_BUDGET`. Section 4 asserts
 * the two are disjoint and total, that the in-scope group names all eleven pages,
 * that an in-scope entry is only ever budgeted at `0`, and that no in-scope file
 * measures a literal. That last one is `it.skip`ped with the single remaining
 * blocker named in a comment beside it; the rest run.
 *
 * The point of the split is what it forbids: an in-scope budget cannot be raised
 * with a note in the PR the way an out-of-scope one can. The only way to make a
 * literal on an in-scope page legal is to change the spec's own M6-M9 list.
 *
 * ===========================================================================
 * METHOD, AND HOW IT WAS VALIDATED
 * ===========================================================================
 * Comments are stripped before counting. Without that, `primitives.jsx` scores
 * two literals it does not render: its header documents the retired `#2962FF`
 * and `#FFB74D` values in prose. A guard that counts documentation would
 * penalise writing any of this down.
 *
 * String literals are NOT stripped, because in this codebase colour literals
 * live inside strings — `style={{ color: "#ef4444" }}` is the dominant form and
 * is exactly what the guard exists to find.
 *
 * The hex pattern requires a non-identifier character after the digits, which
 * is load-bearing: `href="#features"` starts with three valid hex digits and
 * was counted as a colour until that lookahead was added. `components/landing/
 * Footer.jsx` was the file that exposed it — it scored 1 and contains no colour.
 *
 * The functional pattern has a matching lookbehind so `toRgba(` and
 * `parseHsl(` are not colours.
 *
 * The self-tests in "the counting method" below pin all of that down. They are
 * not decoration: a silently broken pattern would make this whole guard pass
 * vacuously, and the budget numbers would drift into fiction.
 *
 * ===========================================================================
 * SCOPE, AND WHY `index.css` IS NOT IN IT
 * ===========================================================================
 * Task 1.10 and §15.1 both scope this guard to `src/pages/**` and
 * `src/components/**`, excluding `styles/tokens.css` and `design/tokens.js`.
 * Task 23.3 added a third root, `src/lib/**`, when it added a pure derivation
 * module there: those two roots are where colour is *rendered*, not the only
 * place it can be chosen, and `lib/blockRegistry.js` — 8 `C.` references in the
 * sibling budget — is the standing proof that a `lib/` module can decide a
 * palette a component merely spreads. All 20 files under `src/lib/` measure 0 as
 * that root is added, so the widening moves no committed number; it means the
 * next `lib/` module that reaches for a hex has to answer for it here.
 *
 * `src/index.css` is in none of the three roots, and it is deliberately left out:
 *
 *   1. It is the token layer, not a consumer of it. It is where `tailwindcss`
 *      and `styles/tokens.css` are imported. The two files task 1.10 names as
 *      exclusions are excluded for exactly this reason — a file whose job is to
 *      declare colour cannot be guarded by a rule that forbids colour — and
 *      index.css is in that class.
 *   2. Of its 29 literals, 22 are inside the landing-page `@utility`
 *      compositions (`btn-primary`, `btn-ghost`, `btn-gold`, `card-surface`,
 *      `card-elevated`, `text-gradient-cyan`, `terminal-frame`, `glass-nav`),
 *      which index.css's own header declares out of scope for v1. The remaining
 *      7 are the base resets: `::selection` (2) and the scrollbar rules (5).
 *      Nothing in this spec is scheduled to lower the 22, so a budget entry for
 *      index.css would be a number that cannot move — a ratchet with no teeth,
 *      and one that mixes the landing page's fate into the trading pages' guard.
 *   3. index.css already has guards suited to its role: task 1.9's
 *      `tokens.generated` check and task 1.3's `@theme`-key lint rule. Those
 *      police the token layer. This one polices consumers.
 *
 * The base resets are a fair question — `::selection` and the scrollbar rules
 * could read `var(--color-...)`, and there is a real argument for making them do
 * so. That would be a change to index.css, which is task 1.4's territory, not a
 * guard's. If it happens, index.css can be brought in scope then; the decision
 * is recorded in `TOKEN_LAYER_FILES` and asserted below so it cannot drift
 * silently into "we just never looked at that file".
 *
 * Test files (`__tests__/`, `*.test.*`, `*.spec.*`) are also outside the scan:
 * a test asserting `expect(...).toBe('#26A69A')` is doing its job. They contain
 * zero literals today, so this changes nothing now — it prevents a future guard
 * from arguing with a future test.
 *
 * ===========================================================================
 * FINDINGS WORTH KNOWING (recorded, not fixed — this task adds no source edits)
 * ===========================================================================
 * * `components/ui-legacy/primitives.jsx` still carries 18 hardcoded `rgba()`
 *   washes inside `Badge`/`Tag`: `rgba(0,200,83,…)` (the retired #00C853
 *   green), `rgba(255,61,0,…)` (#FF3D00), `rgba(255,171,0,…)` (#FFAB00) and
 *   `rgba(124,77,255,…)` (purple). The shim's top-level values are derived from
 *   tokens.css, so `C.profit` is #26A69A — but a profit badge renders that text
 *   against a `rgba(0,200,83,0.3)` border. §14.4 lists "Two greens on screen at
 *   once" as "Impossible after M1: `C.profit` and `--color-status-profit` are
 *   the same value by construction". That holds for the exported scalars and not
 *   for these inline washes. Worth a look when M3 rewrites the primitives.
 * * `components/ui/Badge.jsx` contains 40 occurrences of #10B981 (24) and
 *   #EF4444 (16), not the 25 task 1.4's audit recorded. `pages/Strategies.jsx`
 *   contains 25 such occurrences, of which exactly one — `border-[#ef4444]` on
 *   the failed-strategy card — is the arbitrary-value utility form the audit
 *   cited. The 26/25/1 figures do not reproduce as occurrence counts under any
 *   pattern tried here. Both files are in the budget either way, which is what
 *   task 1.10 requires; the numbers below are the measured ones.
 */

import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, it, expect } from 'vitest';

import {
  COLOUR_LITERAL_BUDGET,
  IN_SCOPE_COLOUR_LITERAL_BUDGET,
  OUT_OF_SCOPE_COLOUR_LITERAL_BUDGET,
  TOKEN_LAYER_FILES,
} from './no-colour-literals.budget.js';
import { SRC, collect, isTestFile, list, relToSrc, stripComments } from './source-scan.js';

const BUDGET_FILE = 'tests/unit/guards/no-colour-literals.budget.js';

/**
 * design.md's M6-M9 page tasks, in the order §14.2 lists the milestones. This is the set
 * Requirement 1.3 is a statement about, written out here so the in-scope group cannot
 * quietly shed a page: `declares the eleven in-scope pages` below compares against it.
 *
 * A page joins this list by gaining a page task in the spec, not by being added here.
 */
const IN_SCOPE_PAGES = Object.freeze([
  'pages/Dashboard.jsx',
  'pages/Portfolio.jsx',
  'pages/Strategies.jsx',
  'pages/TradeHistory.jsx',
  'pages/LiveTrading.jsx',
  'pages/SignalTrace.jsx',
  'pages/Backtester.jsx',
  'pages/StrategyBuilder.jsx',
  'pages/PaperTrading.jsx',
  'pages/StrategyMarketplace.jsx',
  'pages/StrategyDetail.jsx',
]);

/**
 * Task 1.10 / §15.1: this guard polices page and component code. `lib` joined them at
 * task 23.3 — see the SCOPE section above. Every file under `src/lib/` measures 0, so the
 * widening is a fence around clean ground rather than a new debt.
 */
const SCAN_ROOTS = Object.freeze(['pages', 'components', 'lib']);

const SCANNED_EXTENSIONS = Object.freeze(['.js', '.jsx', '.ts', '.tsx', '.css']);

// ---------------------------------------------------------------------------
// Counting
// ---------------------------------------------------------------------------

/**
 * `#RGB`, `#RGBA`, `#RRGGBB`, `#RRGGBBAA`. Longest alternative first so
 * `#00000088` is one 8-digit literal and not `#000000` with `88` left over.
 *
 * The trailing lookahead rejects any identifier character, not just a hex
 * digit: `#features` would otherwise match as `#fea`.
 */
const HEX_LITERAL = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-zA-Z_-])/g;

/** `rgb()`, `rgba()`, `hsl()`, `hsla()`. The lookbehind rejects `toRgba(`. */
const FUNCTIONAL_COLOUR = /(?<![\w$-])(?:rgba?|hsla?)\s*\(/gi;

/**
 * How many colour literals a source file contains.
 *
 * Comments are stripped first (`source-scan.js` explains why that is done
 * without a tokeniser); strings are not, because in this codebase the literals
 * live inside strings.
 */
export function countColourLiterals(source, { lineComments = true } = {}) {
  const code = stripComments(source, { lineComments });
  return [...code.matchAll(HEX_LITERAL)].length + [...code.matchAll(FUNCTIONAL_COLOUR)].length;
}

// ---------------------------------------------------------------------------
// Scanning
// ---------------------------------------------------------------------------

/** `[{ relative, count }]` for every file this guard governs. */
function scan() {
  const files = [];
  for (const root of SCAN_ROOTS) {
    for (const full of collect(path.join(SRC, root), SCANNED_EXTENSIONS)) {
      const relative = relToSrc(full);
      if (isTestFile(relative) || TOKEN_LAYER_FILES.includes(relative)) continue;
      files.push({
        relative,
        count: countColourLiterals(readFileSync(full, 'utf8'), {
          lineComments: path.extname(full) !== '.css',
        }),
      });
    }
  }
  return files.sort((a, b) => a.relative.localeCompare(b.relative));
}

const SCANNED = scan();

// ---------------------------------------------------------------------------
// 1. The counting method itself
// ---------------------------------------------------------------------------

describe('no-colour-literals: the counting method', () => {
  it('counts hex literals of every legal length', () => {
    expect(countColourLiterals('color: #fff')).toBe(1);
    expect(countColourLiterals('color: #fff8')).toBe(1);
    expect(countColourLiterals('color: #F0F2F5')).toBe(1);
    // One 8-digit literal, not #000000 plus a stray 88.
    expect(countColourLiterals('background: #00000088')).toBe(1);
    expect(countColourLiterals('a #26A69A b #EF5350 c #080A0E')).toBe(3);
  });

  it('counts rgb / rgba / hsl / hsla in any casing', () => {
    expect(countColourLiterals('border: 1px solid rgba(0,200,83,0.3)')).toBe(1);
    expect(countColourLiterals('rgb(1,2,3) hsl(1,2%,3%) hsla(1,2%,3%,.4) RGBA(1,2,3,.4)')).toBe(4);
    expect(countColourLiterals('rgba (0, 212, 255, 0.2)')).toBe(1);
  });

  it('does not mistake a fragment identifier for a colour', () => {
    // The bug this lookahead exists for: `#fea` is three valid hex digits.
    expect(countColourLiterals('<a href="#features">')).toBe(0);
    expect(countColourLiterals('href="#faq" href="#about" href="#docs-index"')).toBe(0);
    expect(countColourLiterals('// #region palette')).toBe(0);
    expect(countColourLiterals('<span>#1 by volume</span>')).toBe(0);
  });

  it('does not mistake an identifier ending in rgb or hsl for a colour', () => {
    expect(countColourLiterals('const x = toRgba(c, 0.3)')).toBe(0);
    expect(countColourLiterals('parseHsl(input)')).toBe(0);
  });

  it('ignores a colour named in a comment', () => {
    // Without this, primitives.jsx scores 2 for prose about retired values.
    expect(countColourLiterals('/* was #FFB74D, now token.status.warning */')).toBe(0);
    expect(countColourLiterals('const a = 1; // #2962FF left the palette')).toBe(0);
    expect(countColourLiterals(['/**', ' * #00C853 and rgba(0,200,83,0.3)', ' */'].join('\n'))).toBe(0);
  });

  it('still counts a colour on a line that also carries a comment or a URL', () => {
    expect(countColourLiterals('color: "#ef4444", // loss')).toBe(1);
    expect(countColourLiterals('fetch("https://x.example/y"); const c = "#26A69A";')).toBe(1);
  });

  it('counts colours inside string literals, which is where they live here', () => {
    expect(countColourLiterals('style={{ color: "#ef4444", background: "rgba(0,0,0,.4)" }}')).toBe(2);
    expect(countColourLiterals("className='bg-[#10B981]/10 text-[#10B981]'")).toBe(2);
  });

  it('is not confused by an apostrophe in JSX text', () => {
    // The failure mode that killed the tokeniser this replaced.
    expect(countColourLiterals("<p>Don't stop</p><b style={{ color: '#fff' }} />")).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// 2. Scope
// ---------------------------------------------------------------------------

describe('no-colour-literals: scope', () => {
  it('scans src/pages, src/components and src/lib, and nothing else', () => {
    expect(SCANNED.length).toBeGreaterThan(50);
    for (const { relative } of SCANNED) {
      expect(
        SCAN_ROOTS.some((root) => relative.startsWith(`${root}/`)),
        `${relative} is outside ${SCAN_ROOTS.map((root) => `src/${root}`).join(', ')}`,
      ).toBe(true);
    }
  });

  it('reaches src/lib, the root task 23.3 added', () => {
    // Non-vacuity for the widening: if `collect` did not descend into `src/lib`, the
    // budget entry below would be a stray and the guard would be silently governing
    // nothing there. `lib/drawdownSeries.js` is the file the root was added for.
    const lib = SCANNED.filter((f) => f.relative.startsWith('lib/'));

    expect(lib.length).toBeGreaterThan(1);
    expect(lib.some((f) => f.relative === 'lib/drawdownSeries.js')).toBe(true);
  });

  it('leaves the token layer out, on purpose and on the record', () => {
    for (const relative of TOKEN_LAYER_FILES) {
      expect(existsSync(path.join(SRC, relative)), `${relative} is missing`).toBe(true);
      expect(SCANNED.some((f) => f.relative === relative)).toBe(false);
      expect(Object.keys(COLOUR_LITERAL_BUDGET)).not.toContain(relative);
    }
    // If this ever fails, the header's index.css reasoning needs rewriting
    // rather than the list quietly extending.
    expect(TOKEN_LAYER_FILES).toEqual(['styles/tokens.css', 'design/tokens.js', 'index.css']);
  });

  it('leaves test files out', () => {
    expect(SCANNED.some((f) => isTestFile(f.relative))).toBe(false);
    expect(isTestFile('pages/__tests__/PaperTrading.test.jsx')).toBe(true);
    expect(isTestFile('components/ds/Panel.test.jsx')).toBe(true);
    expect(isTestFile('pages/Dashboard.jsx')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 3. The budget is a ratchet
// ---------------------------------------------------------------------------

describe('no-colour-literals: the decreasing budget', () => {
  it('is well formed', () => {
    for (const [relative, budget] of Object.entries(COLOUR_LITERAL_BUDGET)) {
      expect(Number.isInteger(budget), `${relative}: ${budget} is not an integer`).toBe(true);
      expect(budget, `${relative}: ${budget} is negative`).toBeGreaterThanOrEqual(0);
      expect(relative, `${relative} must be relative to src/ with forward slashes`).toMatch(
        /^[\w.-]+(?:\/[\w.-]+)*$/,
      );
    }
  });

  it('names only files that still exist', () => {
    const gone = Object.keys(COLOUR_LITERAL_BUDGET).filter(
      (relative) => !existsSync(path.join(SRC, relative)),
    );
    expect(
      gone,
      `These budget entries name files that are no longer in src/. A deleted file takes its\n`
        + `entry with it — remove them from ${BUDGET_FILE}:\n${list(gone)}`,
    ).toEqual([]);
  });

  it('accounts for every file that still carries a colour literal', () => {
    const unbudgeted = SCANNED.filter(
      (f) => f.count > 0 && !(f.relative in COLOUR_LITERAL_BUDGET),
    ).map((f) => `${f.relative} — ${f.count} literal(s)`);

    expect(
      unbudgeted,
      `New colour literals in files with no budget entry.\n\n`
        + `Use the design tokens: a Tailwind class from src/styles/tokens.css, or\n`
        + `\`token.*\` from src/design/tokens.js. If this file is genuinely out of scope\n`
        + `for the redesign, add an entry to ${BUDGET_FILE} with a note naming the task\n`
        + `that clears it.\n${list(unbudgeted)}`,
    ).toEqual([]);
  });

  it('holds every file at or below its budget', () => {
    const over = SCANNED.filter(
      (f) => f.relative in COLOUR_LITERAL_BUDGET && f.count > COLOUR_LITERAL_BUDGET[f.relative],
    ).map(
      (f) =>
        `${f.relative} — budget ${COLOUR_LITERAL_BUDGET[f.relative]}, actual ${f.count} `
        + `(+${f.count - COLOUR_LITERAL_BUDGET[f.relative]})`,
    );

    expect(
      over,
      `Colour literals were added to files that are supposed to be shrinking.\n\n`
        + `Replace them with tokens. Raising a budget in ${BUDGET_FILE} reverses\n`
        + `design.md §14.4's monotonic-progress rule and needs a reason in the PR.\n${list(over)}`,
    ).toEqual([]);
  });

  it('requires progress to be recorded, not banked', () => {
    const under = SCANNED.filter(
      (f) => f.relative in COLOUR_LITERAL_BUDGET && f.count < COLOUR_LITERAL_BUDGET[f.relative],
    ).map(
      (f) =>
        `${f.relative} — lower the committed budget from `
        + `${COLOUR_LITERAL_BUDGET[f.relative]} to ${f.count}`,
    );

    expect(
      under,
      `These files now hold fewer colour literals than their committed budget. Good — but\n`
        + `the budget has to come down with them, in this commit, or the headroom stays open\n`
        + `for the literals to creep back in unnoticed. Edit ${BUDGET_FILE}:\n${list(under)}`,
    ).toEqual([]);
  });

  it('has an entry for every file it claims to track, and no strays', () => {
    const scannedNames = new Set(SCANNED.map((f) => f.relative));
    const strays = Object.keys(COLOUR_LITERAL_BUDGET).filter(
      (relative) => !scannedNames.has(relative),
    );
    expect(
      strays,
      `These budget entries name files that exist but are outside this guard's scan\n`
        + `(src/pages and src/components, excluding tests). Remove them from ${BUDGET_FILE}\n`
        + `or widen SCAN_ROOTS deliberately:\n${list(strays)}`,
    ).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 4. The in-scope set is closed — task 27.3
// ---------------------------------------------------------------------------

describe('no-colour-literals: the in-scope set is closed', () => {
  const inScope = Object.keys(IN_SCOPE_COLOUR_LITERAL_BUDGET);
  const outOfScope = Object.keys(OUT_OF_SCOPE_COLOUR_LITERAL_BUDGET);

  it('splits the budget in two, with nothing in both groups and nothing in neither', () => {
    // The flat map is `{...inScope, ...outOfScope}`, so a duplicate key would not be
    // visible there — it would just silently take the out-of-scope number. This is the
    // assertion that makes the spread lossless.
    const both = inScope.filter((relative) => relative in OUT_OF_SCOPE_COLOUR_LITERAL_BUDGET);
    expect(
      both,
      `These entries are declared in BOTH groups of ${BUDGET_FILE}. The flat map takes the\n`
        + `out-of-scope number for them, so the in-scope emptiness rule would not apply:\n`
        + `${list(both)}`,
    ).toEqual([]);

    expect(inScope.length + outOfScope.length).toBe(Object.keys(COLOUR_LITERAL_BUDGET).length);
    expect(new Set([...inScope, ...outOfScope])).toEqual(
      new Set(Object.keys(COLOUR_LITERAL_BUDGET)),
    );
  });

  it('declares the eleven in-scope pages, and no page from that list out of scope', () => {
    const missing = IN_SCOPE_PAGES.filter(
      (relative) => !(relative in IN_SCOPE_COLOUR_LITERAL_BUDGET),
    );
    expect(
      missing,
      `design.md gives these pages a page task in M6-M9, so they belong in\n`
        + `IN_SCOPE_COLOUR_LITERAL_BUDGET. An in-scope page with no in-scope entry is a page\n`
        + `the emptiness rule below does not reach:\n${list(missing)}`,
    ).toEqual([]);

    const misfiled = IN_SCOPE_PAGES.filter(
      (relative) => relative in OUT_OF_SCOPE_COLOUR_LITERAL_BUDGET,
    );
    expect(
      misfiled,
      `These in-scope pages are filed as out of scope in ${BUDGET_FILE}. Moving a page out\n`
        + `of scope is a change to the spec's M6-M9 list, not to this file:\n${list(misfiled)}`,
    ).toEqual([]);

    // Non-vacuity: if IN_SCOPE_PAGES were ever emptied, the two checks above would pass
    // over nothing.
    expect(IN_SCOPE_PAGES).toHaveLength(11);
  });

  it('names only files that still exist, in both groups', () => {
    // `names only files that still exist` covers the flat map; this repeats it per group so
    // a stale entry is reported against the group whose rule it breaks. Task 27.3 deleted
    // `components/DesktopOnlyOverlay.jsx` and removed its out-of-scope entry.
    const gone = [...inScope, ...outOfScope].filter(
      (relative) => !existsSync(path.join(SRC, relative)),
    );
    expect(gone, `Entries naming files no longer in src/:\n${list(gone)}`).toEqual([]);
  });

  it('never raises an in-scope entry above zero, whatever the file measures', () => {
    // The rule the split exists for, stated over the COMMITTED numbers rather than the
    // measured ones. It carried a single declared exception — `pages/StrategyMarketplace.jsx`
    // at 182 — from task 27.3's first part until the page was migrated; that exception is
    // gone, so the expectation is now `[]` and no in-scope entry has headroom of any size.
    // An in-scope file may not be *given* headroom: the only legal in-scope budget is 0.
    const withHeadroom = inScope
      .filter((relative) => IN_SCOPE_COLOUR_LITERAL_BUDGET[relative] > 0)
      .map((relative) => `${relative} — ${IN_SCOPE_COLOUR_LITERAL_BUDGET[relative]}`);

    expect(
      withHeadroom,
      `An in-scope file may only be budgeted at 0 — Requirement 1.3 is a statement about\n`
        + `exactly these files. Use a token: a Tailwind class from src/styles/tokens.css, or\n`
        + `\`token.*\` from src/design/tokens.js. If the file is genuinely out of scope, move\n`
        + `its entry to OUT_OF_SCOPE_COLOUR_LITERAL_BUDGET in ${BUDGET_FILE} with a reason.\n`
        + `${list(withHeadroom)}`,
    ).toEqual([]);
  });

  /**
   * THE ASSERTION THIS TASK EXISTS TO ADD. IT IS NOW LIVE.
   *
   * `pages/StrategyMarketplace.jsx` measured 182 when task 27.3's structural half landed:
   * task 26.1 had cleared the subscription-state element — the only part of that page
   * Requirement 13 governs — and the 182 that remained were the catalogue and detail chrome.
   * It was the last in-scope entry above zero, so this `it` was committed skipped rather
   * than made to pass by editing a list.
   *
   * That precondition is met. The chrome is on the token layer, the entry is `0`, and this
   * assertion is un-skipped in the same commit — paired, as the note it replaces required,
   * with dropping the `withHeadroom` expectation above to `[]`. The in-scope set is empty
   * because every page in it was migrated, not because an entry was deleted: `never raises
   * an in-scope entry above zero` and `names only files that still exist` are what keep
   * those two facts from being confused, and both run beside this one.
   *
   * Note what each of the pair does now. The one above reads the COMMITTED numbers, so it
   * refuses headroom being written down; this one reads the MEASURED counts, so it refuses a
   * literal that is really in the tree. Neither subsumes the other — a literal added to an
   * in-scope page with its budget left at 0 is caught only here.
   */
  it('holds no colour literal in any in-scope file', () => {
    const dirty = SCANNED.filter(
      (f) => f.relative in IN_SCOPE_COLOUR_LITERAL_BUDGET && f.count > 0,
    ).map((f) => `${f.relative} — ${f.count} literal(s)`);

    expect(
      dirty,
      `Requirement 1.3: every in-scope page and shared surface holds its colour in the\n`
        + `token layer. These do not:\n${list(dirty)}`,
    ).toEqual([]);
  });
});
