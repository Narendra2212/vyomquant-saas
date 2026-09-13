/**
 * ═══════════════════════════════════════════════════════════════════════════
 * The accessibility ratchet's teeth — vyomquant-ui-redesign task 6.27
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Requirements 18.1, 18.4. design.md §11.7, §19.
 *
 * `eslint.config.js` raises every `jsx-a11y-x` rule to `error` for
 * `src/components/ds/**` and `src/pages/**`, and downgrades a named, counted list of
 * un-migrated in-scope pages to `warn` (`eslint-rules/a11y-ratchet.js`).
 *
 * A config alone cannot make that a ratchet. It can say "these five pages are exempt";
 * it cannot notice when one of them stops needing to be. A page task could rebuild
 * `Strategies.jsx`, clear its five findings, leave the waiver line in place, and nothing
 * would complain — and the next contributor could put five findings back into a file
 * everyone now believes is migrated. That is the same hole
 * `legacy-c-budget.test.js` and `no-colour-literals.test.js` exist to close, so this
 * guard closes it the same way: the recorded count must be EXACT, in both directions.
 *
 *   over count  — a new violation entered a waived page. Fix it; do not raise the number.
 *   under count — violations were fixed and the waiver was left where it was. Lower it,
 *                 in the same commit, or delete the line if it reached zero.
 *
 * Plus the two ends the waiver list does not cover:
 *
 *   * `src/components/ds/**` must be at EXACTLY ZERO. This is the half of task 6.27 that
 *     is a claim about today rather than a promise about later: no `div onClick` on any
 *     interactive element (§11.7), enforced rather than remembered. It is asserted here
 *     as well as in the lint so that a primitive that grows one fails the suite a
 *     contributor already runs.
 *   * every in-scope page NOT on the waiver list must be at ZERO. This is what makes the
 *     list unskippable: a page rebuilt by its migration task, or a page added tomorrow, is
 *     held to the rule with no action at all. Skipping requires adding a line here, which
 *     a reviewer sees.
 *
 * ═══ WHY IT RUNS ESLINT RATHER THAN GREPPING ═══
 *
 * The other guards in this directory count textual occurrences, because a hex literal is a
 * hex literal. An a11y finding is not textual: whether `<div onClick>` reports depends on
 * the element, its role, its other handlers and its `tabIndex`. Reimplementing that here
 * would produce a second, worse copy of the plugin, and a guard whose numbers disagree
 * with the linter's is worse than no guard. So this asks the same ESLint that CI runs,
 * through the same `eslint.config.js`, and reads the severities the config actually
 * resolved.
 *
 * Costs a few seconds. `fileParallelism: false` (vitest.config.js) means that is wall
 * clock, and it is the price of the numbers being real.
 */

import { describe, expect, it } from 'vitest';
import { ESLint } from 'eslint';

import {
  A11Y_PAGE_WAIVERS,
  A11Y_RULES_ADDED,
  A11Y_RULES_NOT_RAISED,
  A11Y_RULES_RAISED_FROM_OFF,
  a11yRules,
} from '../../../eslint-rules/a11y-ratchet.js';
import { TERMINAL_ROOT, list, toPosix } from './source-scan.js';

const WAIVER_FILE = 'eslint-rules/a11y-ratchet.js';

/**
 * Requirement 1.3's in-scope page list, as filenames.
 *
 * `LiveTrading.jsx` (task 20.1) does not exist yet and `Dashboard.jsx` / `StrategyBuilder.jsx`
 * / `TradeHistory.jsx` / `PaperTrading.jsx` report zero today. All are listed anyway: the
 * point of the set is that a page joining it is enforced, and a file that is absent is
 * simply not linted.
 */
const IN_SCOPE_PAGES = Object.freeze([
  'src/pages/Dashboard.jsx',
  'src/pages/LiveTrading.jsx',
  'src/pages/Strategies.jsx',
  'src/pages/StrategyDetail.jsx',
  'src/pages/StrategyBuilder.jsx',
  'src/pages/Backtester.jsx',
  'src/pages/SignalTrace.jsx',
  'src/pages/Portfolio.jsx',
  'src/pages/TradeHistory.jsx',
  'src/pages/PaperTrading.jsx',
  'src/pages/StrategyMarketplace.jsx',
]);

// ---------------------------------------------------------------------------
// The measurement
// ---------------------------------------------------------------------------

const isA11y = (message) => typeof message.ruleId === 'string' && message.ruleId.startsWith('jsx-a11y');

/**
 * `{ 'src/pages/X.jsx': { count, errors, warnings, rules: [...] } }` for one glob.
 *
 * Keyed relative to the terminal root and forward-slashed, so the keys read the same as
 * the waiver list's on every platform.
 */
async function measure(patterns) {
  const eslint = new ESLint({ cwd: TERMINAL_ROOT, errorOnUnmatchedPattern: false });
  const results = await eslint.lintFiles(patterns);

  const byFile = {};
  for (const result of results) {
    const relative = toPosix(result.filePath.slice(TERMINAL_ROOT.length + 1));
    const found = result.messages.filter(isA11y);
    byFile[relative] = {
      count: found.length,
      errors: found.filter((m) => m.severity === 2).length,
      warnings: found.filter((m) => m.severity === 1).length,
      rules: [...new Set(found.map((m) => `${m.ruleId} (line ${m.line})`))].sort(),
    };
  }
  return byFile;
}

const DS = await measure(['src/components/ds/**/*.{js,jsx}']);
const PAGES = await measure(['src/pages/**/*.{js,jsx}']);

// ---------------------------------------------------------------------------
// 1. `ds/` is clean, and at error
// ---------------------------------------------------------------------------

describe('a11y ratchet: the ds/ primitives are clean', () => {
  it('lints at least the primitives this spec builds', () => {
    // A guard that measured nothing would pass vacuously — the failure mode
    // `source-scan.js`'s header calls the worst outcome a guard can have.
    expect(Object.keys(DS).length).toBeGreaterThan(20);
    expect(Object.keys(DS)).toContain('src/components/ds/Alert.jsx');
    expect(Object.keys(DS)).toContain('src/components/ds/StatusBadge.jsx');
  });

  it('reports zero accessibility findings across every primitive', () => {
    const offenders = Object.entries(DS)
      .filter(([, m]) => m.count > 0)
      .map(([file, m]) => `${file}: ${m.count}\n${list(m.rules)}`);

    expect(
      offenders,
      `ds/ must stay at zero accessibility findings (task 6.27, Requirements 18.1, 18.4).\n`
        + `§11.7 allows no \`div onClick\` on an interactive element: give it a real \`<button>\`,\n`
        + `or a \`role\` plus \`tabIndex={0}\` and an \`onKeyDown\`, as \`ds/Chart\`'s cursor does.\n`
        + `Do NOT add a waiver — the waiver list in ${WAIVER_FILE} is for un-migrated pages,\n`
        + `and every page task depends on these primitives already being clean.\n${list(offenders)}`,
    ).toEqual([]);
  });

  it('holds them at error, not warn', () => {
    // If the enforced block's globs stopped matching `ds/`, the assertion above would
    // still pass — zero findings is zero findings at any severity. This is what notices.
    const resolved = a11yRules('error');
    // `severityOf`, not a bare equality: most of these rules carry the preset's options,
    // so the entry is `['error', {…}]`. Keeping those options is the point of deriving the
    // map from the preset rather than retyping it, and an assertion that only accepted the
    // bare string would be an assertion that they had been thrown away.
    const severityOf = (entry) => (Array.isArray(entry) ? entry[0] : entry);

    expect(Object.keys(resolved).length).toBeGreaterThan(30);
    expect(Object.values(resolved).every((v) => severityOf(v) === 'error')).toBe(true);

    // The three that carry §11.7's "no `div onClick`" between them.
    for (const name of [
      'jsx-a11y-x/click-events-have-key-events',
      'jsx-a11y-x/no-static-element-interactions',
      'jsx-a11y-x/no-noninteractive-element-interactions',
    ]) {
      expect(severityOf(resolved[name]), `${name} carries §11.7's div-onClick rule`).toBe('error');
    }
    // Options survived the severity change.
    expect(resolved['jsx-a11y-x/no-static-element-interactions'][1].handlers).toContain('onClick');

    for (const name of [...A11Y_RULES_ADDED, ...A11Y_RULES_RAISED_FROM_OFF]) {
      expect(resolved[name], `${name} must be enforced`).toBeDefined();
      expect(severityOf(resolved[name])).toBe('error');
    }
    for (const name of A11Y_RULES_NOT_RAISED) {
      expect(resolved[name], `${name} is deliberately not raised — see ${WAIVER_FILE}`).toBeUndefined();
    }
  });
});

// ---------------------------------------------------------------------------
// 2. The waiver is well formed and self-expiring
// ---------------------------------------------------------------------------

describe('a11y ratchet: the waiver list', () => {
  it('is well formed', () => {
    for (const [file, entry] of Object.entries(A11Y_PAGE_WAIVERS)) {
      expect(file, `${file} must be relative to the terminal root with forward slashes`).toMatch(
        /^src\/pages\/[\w.-]+$/,
      );
      expect(Number.isInteger(entry.count), `${file}: ${entry.count} is not an integer`).toBe(true);
      expect(entry.count, `${file}: a waiver of 0 must be deleted, not recorded`).toBeGreaterThan(0);
      expect(entry.task, `${file} needs the task number that deletes it`).toMatch(/^\d+\.\d+$/);
    }
  });

  it('waives only in-scope pages, so every entry has a task that clears it', () => {
    // Requirement 18 is scoped to in-scope pages. A waiver for a page with no scheduled
    // task would be a number nothing is going to lower — a ratchet with no teeth, and the
    // reason `Landing.jsx`, `ExchangeManager.jsx`, `Profile.jsx` and `RiskSettings.jsx`
    // are absent from the list despite carrying findings.
    const outOfScope = Object.keys(A11Y_PAGE_WAIVERS).filter((f) => !IN_SCOPE_PAGES.includes(f));
    expect(
      outOfScope,
      `Only in-scope pages (Requirement 1.3) may be waived, because only they have a\n`
        + `rebuild task to clear them. To hold an out-of-scope page to the rule, fix it —\n`
        + `do not record a number no task will lower.\n${list(outOfScope)}`,
    ).toEqual([]);
  });

  it('names only files that still exist', () => {
    const missing = Object.keys(A11Y_PAGE_WAIVERS).filter((f) => !(f in PAGES));
    expect(
      missing,
      `These waivers name files that are not there any more. Delete the lines.\n${list(missing)}`,
    ).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 3. The counts are exact, in both directions
// ---------------------------------------------------------------------------

describe('a11y ratchet: the recorded counts are the real ones', () => {
  for (const [file, entry] of Object.entries(A11Y_PAGE_WAIVERS)) {
    it(`${file} holds exactly ${entry.count} (cleared by task ${entry.task})`, () => {
      const measured = PAGES[file];
      expect(measured, `${file} was not linted`).toBeDefined();

      expect(
        measured.count,
        measured.count > entry.count
          ? `${file} gained ${measured.count - entry.count} accessibility finding(s).\n`
            + `The waiver is a ceiling coming down, not a licence. Fix the new one:\n${list(measured.rules)}`
          : `${file} is down to ${measured.count} from ${entry.count}. Record it: set \`count\`\n`
            + `to ${measured.count} in ${WAIVER_FILE}${measured.count === 0 ? ' — or rather, delete the entry entirely, since the page is clean and belongs at `error` now' : ''}.\n`
            + `Progress has to be recorded to count, or the next contributor can put it back.`,
      ).toBe(entry.count);

      // Waived means `warn`, not `off`. A finding that stopped being reported at all would
      // satisfy an equality check on a count of zero and hide the debt.
      expect(measured.warnings, `${file}'s findings must be reported, at warn`).toBe(entry.count);
      expect(measured.errors).toBe(0);
    });
  }
});

// ---------------------------------------------------------------------------
// 4. An in-scope page that is not waived is at zero
// ---------------------------------------------------------------------------

describe('a11y ratchet: no page can be quietly skipped', () => {
  it('holds every unwaived in-scope page at zero findings', () => {
    const offenders = IN_SCOPE_PAGES.filter((f) => f in PAGES)
      .filter((f) => !(f in A11Y_PAGE_WAIVERS))
      .filter((f) => PAGES[f].count > 0)
      .map((f) => `${f}: ${PAGES[f].count}\n${list(PAGES[f].rules)}`);

    expect(
      offenders,
      `An in-scope page with no waiver must be at zero (Requirements 18.1, 18.4).\n`
        + `If this page has just been migrated, fix the findings — that is what the\n`
        + `migration task is for. Adding a waiver line is a deliberate, reviewable act,\n`
        + `and it needs the task number that removes it.\n${list(offenders)}`,
    ).toEqual([]);
  });

  it('records what M7-M9 is walking into', () => {
    // Not an assertion about quality — a checked-in figure so the milestone that inherits
    // this debt cannot be surprised by its size. Waived in-scope pages only; the
    // out-of-scope pages' findings are pre-existing and belong to no task here.
    //
    // 25 -> 23 at task 16.2, which deleted `src/pages/Portfolio.jsx`'s entry rather than
    // lowering it. Its two findings were both `no-redundant-roles`, on the allocation legend's
    // `<ul role="list">` / `<li role="listitem">`; the legend is now a `ds/DataTable`, which is
    // not a list and carries no role attribute. The page lints at `error` from here.
    const waived = Object.values(A11Y_PAGE_WAIVERS).reduce((sum, e) => sum + e.count, 0);
    const measured = Object.keys(A11Y_PAGE_WAIVERS).reduce((sum, f) => sum + PAGES[f].count, 0);
    expect(measured).toBe(waived);
    expect(waived).toBe(23);
    // And the cleared page is really clean, at `error`, with no line left behind.
    expect(A11Y_PAGE_WAIVERS['src/pages/Portfolio.jsx']).toBeUndefined();
    expect(PAGES['src/pages/Portfolio.jsx'].count).toBe(0);
  });
});
