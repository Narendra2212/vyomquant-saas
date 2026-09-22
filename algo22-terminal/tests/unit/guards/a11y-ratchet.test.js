/**
 * ═══════════════════════════════════════════════════════════════════════════
 * The accessibility ratchet's teeth — vyomquant-ui-redesign task 6.27
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Requirements 18.1, 18.4. design.md §11.7, §19.
 *
 * `eslint.config.js` raises every `jsx-a11y-x` rule to `error` for
 * `src/components/ds/**`, `src/components/landing/**` and `src/pages/**`, and downgrades a
 * named, counted list of un-migrated in-scope pages to `warn`
 * (`eslint-rules/a11y-ratchet.js`).
 *
 * `src/components/landing/**` and every page outside the original eleven joined at
 * retail-ui-simplification task 4.1 (Requirement 6.1) — see {@link IN_SCOPE_PAGES} for what
 * grew, what it corrects in that requirement, and why the landing surface was the only real
 * gap in the lint rather than eleven pages' worth. The same task replaced this file's one
 * hardcoded total with a derived one (Decision D7, design.md §7.4): a checked-in figure goes
 * red on the commit that finally clears the debt, and Requirement 12.3 calls that a defect in
 * the guard rather than something to work around with a `0` entry.
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
 * Every page a trader can open — retail-ui-simplification task 4.1, Requirement 6.1.
 *
 * ═══ WHAT THIS LIST WAS, AND WHY IT GREW ═══
 *
 * It held the eleven `In_Scope_Pages` of `vyomquant-ui-redesign` (Requirement 1.3):
 * `Dashboard`, `LiveTrading`, `Strategies`, `StrategyDetail`, `StrategyBuilder`,
 * `Backtester`, `SignalTrace`, `Portfolio`, `TradeHistory`, `PaperTrading`,
 * `StrategyMarketplace` — the pages that spec was rebuilding. The other eleven under
 * `src/pages/` (`Unmigrated_Pages`) and the whole landing surface were outside it, and
 * an unlisted file is a file this guard does not hold: `holds every unwaived in-scope
 * page at zero findings` never reads it, so nothing noticed if one gained a finding.
 * A keyboard-only trader can reach Risk Settings, so a kill switch they cannot operate
 * there is the same defect as one on Dashboard.
 *
 * It is now **every file under `src/pages/` plus the fifteen `Landing_Surface` files**
 * — `LandingPage.jsx` and the fourteen sections it renders — which is Requirement 6.1's
 * scope, measured.
 *
 * ═══ A CORRECTION TO REQUIREMENT 6.1, AND ONE TO §1.4 ═══
 *
 * **Requirement 6.1 says "the 14 rendered `Landing_Surface` sections"; with
 * `LandingPage.jsx` itself that is fifteen files, and the definition's "13 sections"
 * undercounts its own list.** `LandingPage.jsx:2`–`:15` imports fourteen: `Navbar`,
 * `Hero`, `TrustSection`, `ScreenshotsSection`, `HowItWorks`, `ModernTradingSection`,
 * `SecuritySection`, `FounderSection`, `DownloadSection`, `Pricing`, `FAQ`, `Waitlist`,
 * `FinalCTA`, `Footer`. 23 files sit in `src/components/landing/`; the eight requirements
 * §1.6 records as imported by nothing are deliberately absent here, because a guard that
 * held dead files to a standard would spend its teeth on markup no visitor reaches
 * (Requirement 14.2's argument, applied to this list instead of to a budget).
 *
 * **§1.4's table says the eleven `Unmigrated_Pages` are "not linted at all". They are.**
 * `A11Y_ENFORCED_GLOBS` has carried `src/pages/**` at `error` since task 6.27, so every
 * page in this list was already linted at `error` — what was missing was this list, and
 * so the ratchet. They were **linted but not held**: incidentally clean, with nothing
 * asserting they stay that way. The landing surface was the real gap in the lint, and it
 * is the narrower one than §1.4 implies — `jsxA11y.configs.recommended` applies to every
 * linted file with no `files` key, so its 31 `error` rules already reached the sections;
 * the four that did not are the two the preset ships `off`
 * ({@link A11Y_RULES_RAISED_FROM_OFF}) and the two it never configures
 * ({@link A11Y_RULES_ADDED}), one of which is Requirement 18.4 verbatim. Task 4.1 adds
 * `src/components/landing/**` to the enforced globs for exactly those four.
 *
 * ═══ WHAT IS NOT LISTED, AND WHY ═══
 *
 * `src/pages/paperTradingFormat.js`, `src/pages/tradeHistoryFilters.js` and
 * `src/pages/__tests__/**` are under `src/pages/` and are not here. They hold no JSX, so
 * no `jsx-a11y` rule can fire in them and an entry would assert nothing. They are linted
 * at `error` regardless, through the glob — the glob is what lints, this list is what
 * *holds*, and the distinction is the one §1.4 blurred.
 *
 * `pages/Landing.jsx` IS listed, though it carries a `DEPRECATED / UNMOUNTED` header and
 * `App.jsx:590` routes `components/landing/LandingPage` at `/` instead. It is in the tree
 * and the guard reports what is there; task 9.2 deletes the file, and `every file in this
 * list is actually linted` is what fails if that lands and this line stays.
 */
const IN_SCOPE_PAGES = Object.freeze([
  // The eleven `In_Scope_Pages` this list started as.
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
  // The eleven `Unmigrated_Pages`, in Requirement 4.5's migration order so the list
  // reads as the work does: RiskSettings first, AuthPage last, Landing per Req 14.
  'src/pages/RiskSettings.jsx',
  'src/pages/TwoFA.jsx',
  'src/pages/SecurityLogs.jsx',
  'src/pages/UpdatePasswordPage.jsx',
  'src/pages/Wizard.jsx',
  'src/pages/LegalPage.jsx',
  'src/pages/Profile.jsx',
  'src/pages/Billing.jsx',
  'src/pages/ExchangeManager.jsx',
  'src/pages/AuthPage.jsx',
  'src/pages/Landing.jsx',
  // `Landing_Surface` — what `App.jsx:590` actually serves at `/`, in the order
  // `LandingPage.jsx` renders them.
  'src/components/landing/LandingPage.jsx',
  'src/components/landing/Navbar.jsx',
  'src/components/landing/Hero.jsx',
  'src/components/landing/TrustSection.jsx',
  'src/components/landing/ScreenshotsSection.jsx',
  'src/components/landing/HowItWorks.jsx',
  'src/components/landing/ModernTradingSection.jsx',
  'src/components/landing/SecuritySection.jsx',
  'src/components/landing/FounderSection.jsx',
  'src/components/landing/DownloadSection.jsx',
  'src/components/landing/Pricing.jsx',
  'src/components/landing/FAQ.jsx',
  'src/components/landing/Waitlist.jsx',
  'src/components/landing/FinalCTA.jsx',
  'src/components/landing/Footer.jsx',
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

/**
 * Every enforced page surface, in one map.
 *
 * `src/components/landing/**` joined it at task 4.1 with {@link IN_SCOPE_PAGES}: a list
 * entry whose file is not measured is an entry this guard silently skips, so the scan has
 * to widen with the list or the extension would be decorative. Still called `PAGES`
 * because that is what these files are to a trader — the landing surface is the first page
 * they see.
 */
const PAGES = await measure([
  'src/pages/**/*.{js,jsx}',
  'src/components/landing/**/*.{js,jsx}',
]);

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
      // `src/components/landing/` became waivable at task 4.1, when the landing surface
      // joined IN_SCOPE_PAGES. No section needs a waiver today — all fifteen measure zero
      // — and the pattern is widened so that a future finding there can be recorded
      // honestly rather than absorbed by lowering a severity.
      expect(file, `${file} must be relative to the terminal root with forward slashes`).toMatch(
        /^src\/(?:pages|components\/landing)\/[\w.-]+$/,
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
  it('lints every file in the list, so a typo cannot exempt a page', () => {
    // `holds every unwaived in-scope page at zero findings` filters on `f in PAGES`, so a
    // misspelled entry is skipped in silence. That was tolerable at eleven hand-written
    // entries and is not at 37. Every file named above exists and is linted today; when
    // task 9.2 deletes `pages/Landing.jsx`, this is what requires its line to go too.
    const unlinted = IN_SCOPE_PAGES.filter((f) => !(f in PAGES));
    expect(
      unlinted,
      `These entries name files the lint does not reach — a misspelling, or a file that has\n`
        + `been deleted or moved. Either way the page is NOT being held, which is what this\n`
        + `list exists to prevent. Fix the path or delete the line:\n${list(unlinted)}`,
    ).toEqual([]);

    // Non-vacuity, and Requirement 6.1's scope as a number: 22 pages + 15 landing files.
    expect(IN_SCOPE_PAGES).toHaveLength(37);
    expect(new Set(IN_SCOPE_PAGES).size).toBe(IN_SCOPE_PAGES.length);
  });

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

  it('accounts for all the accessibility debt there is, and derives the total', () => {
    // Was `records what M7-M9 is walking into`, and ended `expect(waived).toBe(4)` — a
    // checked-in total whose stated purpose was that the milestone inheriting this debt
    // could not be surprised by its size. **Decision D7 (design.md §7.4) replaces it with
    // a derived total, and Requirement 12.3 is why:** a hardcoded figure goes red on every
    // change that moves the number, including the one that moves it to zero, so the guard
    // would fail at the exact moment the debt was finally cleared. A guard that breaks when
    // it succeeds gets deleted rather than fixed.
    //
    // Task 4.1 is the change that hits it first — Requirement 6.1's extension was expected
    // to move `waived` off 4 by seeding the newly-linted pages' findings — and it turns out
    // the extension added no entry at all, because every one of the 26 newly-held files
    // measures zero. So the stale total did NOT fail here, which is the weaker of the two
    // reasons to make this edit and not a reason to defer it: task 5.3 deletes the last
    // entry, and on that commit `toBe(4)` fails with nothing wrong.
    //
    // What replaces it is the assertion the number was standing in for. The debt recorded
    // in the waiver list equals ALL the a11y debt in the enforced scope — every finding on
    // every page and primitive is on a waived page, at `warn`, with a task against it. That
    // holds in both directions, on any list, at any total, the empty list included: at zero
    // entries it says the enforced scope has no findings anywhere, which is precisely the
    // end state. No number to update, and nothing left unaccounted for.
    //
    // 25 -> 23 at task 16.2, which deleted `src/pages/Portfolio.jsx`'s entry rather than
    // lowering it. Its two findings were both `no-redundant-roles`, on the allocation legend's
    // `<ul role="list">` / `<li role="listitem">`; the legend is now a `ds/DataTable`, which is
    // not a list and carries no role attribute. The page lints at `error` from here.
    //
    // 23 -> 21 at task 17.1, which rebuilt `src/pages/Strategies.jsx`'s owner card grid: the
    // two that went were `click-events-have-key-events` and `no-static-element-interactions`
    // on the dashed "Create New Strategy" tile, a `div onClick` that the `ds/EmptyState`'s
    // own action and the header's `New strategy` command replace. Its entry was LOWERED rather
    // than deleted then — the remaining 3 were the deploy modal's
    // `label-has-associated-control` cluster, which task 17.2 owned.
    //
    // 21 -> 18 at task 17.2's last part, which DELETED that entry rather than lowering it. The
    // three were on the deploy modal's Execution Mode, Capital and Trade Size labels; the
    // modal is now a `ds/ConfirmDialog` whose four controls are all `ds/Field`, and `ds/Field`
    // renders a visible `<label htmlFor>` with no hidden-label option. The page lints at
    // `error` from here, which is what removing the line does.
    //
    // 18 -> 8 at task 21.4a, which DELETED `src/pages/SignalTrace.jsx`'s entry — the largest
    // one — rather than lowering it. Eight of its ten were the `label-has-associated-control`
    // cluster on the filter grid's bare `<label>` elements, three of which labelled their
    // input by `placeholder` alone; the filters are `ds/FilterBar` now, so every control is a
    // `ds/Field` with a visible `<label htmlFor>`. The other two were
    // `click-events-have-key-events` and `no-static-element-interactions` on the signal row's
    // `div onClick`, and the rows are `ds/DataTable`'s. The page lints at `error` from here.
    //
    // 8 -> 4 at task 23.1, which DELETED `src/pages/Backtester.jsx`'s entry rather than
    // lowering it. Three of its four were `label-has-associated-control` on the configuration
    // column's bare `<label>`s, and the fourth was `control-has-associated-label` on the
    // timeframe `<select>` none of them named — the only finding this ratchet's added rules
    // contributed on any page. The configuration flow is four `ds/Panel`s of `ds/Field`s now,
    // and `ds/Field` renders a visible `<label htmlFor>` with no hidden-label option. The page
    // lints at `error` from here, results region included.
    //
    // 4 -> 4 at task 4.1, the widening itself: 26 files joined the held set — the eleven
    // `Unmigrated_Pages` and the fifteen `Landing_Surface` files — and every one of them
    // measures ZERO, so no entry was seeded and the total did not move. Worth recording
    // precisely because it is the outcome nobody predicted: §1.4 called those eleven pages
    // "not linted at all", and what they actually were is linted at `error` and clean.
    //
    // 4 -> 0 is task 5.3's, which deletes `StrategyMarketplace.jsx`'s entry after task 5.2
    // rebuilds both interactive cards. It is the last line in the list, so that commit also
    // takes the waiver block out of `eslint.config.js` and this section with it. The total
    // is no longer asserted by value, so the sequence above is a record rather than a
    // number this test is checked against — which is the whole of Decision D7.
    const waived = Object.values(A11Y_PAGE_WAIVERS).reduce((sum, e) => sum + e.count, 0);
    const measured = Object.keys(A11Y_PAGE_WAIVERS).reduce((sum, f) => sum + PAGES[f].count, 0);
    expect(measured).toBe(waived);

    // D7's derived total. Every a11y finding anywhere in the enforced scope is accounted
    // for by a waiver entry — so the recorded debt IS the debt, and `waived` needs no
    // checked-in value to be meaningful. `DS` is summed in as well as `PAGES`: it is
    // asserted at zero above, and summing it here means a primitive that grew a finding
    // would have to fail this too, with no waiver able to absorb it (the waiver pattern
    // admits `src/pages/` and `src/components/landing/` only).
    const findingsEverywhere =
      Object.values(PAGES).reduce((sum, m) => sum + m.count, 0)
      + Object.values(DS).reduce((sum, m) => sum + m.count, 0);
    expect(
      findingsEverywhere,
      `The enforced scope holds ${findingsEverywhere} accessibility finding(s) and the waiver\n`
        + `list records ${waived}. Every finding must be on a waived page with a task against it:\n`
        + `either fix the unaccounted one, or — if it is on an in-scope page that has not been\n`
        + `migrated yet — record it in ${WAIVER_FILE} with the task number that clears it.\n`
        + `Do NOT lower a severity to close the gap.`,
    ).toBe(waived);

    // Waived means `warn`; nothing in the enforced scope reports at `error`, which is what
    // keeps `npm run lint` honest about this rule set. Asserted over the whole scope rather
    // than per waiver, so a finding on an UNWAIVED page cannot hide here either.
    expect(Object.values(PAGES).reduce((sum, m) => sum + m.errors, 0)).toBe(0);
    expect(Object.values(DS).reduce((sum, m) => sum + m.errors, 0)).toBe(0);

    // Non-vacuity: `findingsEverywhere === waived` is satisfied by measuring nothing at
    // all, which is the one way this could pass while enforcing nothing.
    expect(Object.keys(PAGES).length).toBeGreaterThanOrEqual(IN_SCOPE_PAGES.length);

    // And the cleared pages are really clean, at `error`, with no line left behind.
    for (const page of [
      'src/pages/Portfolio.jsx',
      'src/pages/Strategies.jsx',
      'src/pages/SignalTrace.jsx',
      'src/pages/Backtester.jsx',
    ]) {
      expect(A11Y_PAGE_WAIVERS[page]).toBeUndefined();
      expect(PAGES[page].count).toBe(0);
    }
  });
});
