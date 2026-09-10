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
 * `src/index.css` is in neither root, and it is deliberately left out:
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

import { COLOUR_LITERAL_BUDGET, TOKEN_LAYER_FILES } from './no-colour-literals.budget.js';
import { SRC, collect, isTestFile, list, relToSrc, stripComments } from './source-scan.js';

const BUDGET_FILE = 'tests/unit/guards/no-colour-literals.budget.js';

/** Task 1.10 / §15.1: this guard polices page and component code. */
const SCAN_ROOTS = Object.freeze(['pages', 'components']);

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
  it('scans src/pages and src/components, and nothing else', () => {
    expect(SCANNED.length).toBeGreaterThan(50);
    for (const { relative } of SCANNED) {
      expect(
        SCAN_ROOTS.some((root) => relative.startsWith(`${root}/`)),
        `${relative} is outside src/pages and src/components`,
      ).toBe(true);
    }
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
