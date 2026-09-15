/**
 * ═══════════════════════════════════════════════════════════════════════════
 * The accessibility ratchet — one list, read by the linter and by the guard
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.27. design.md §11.7, §19. Requirements 18.1, 18.4.
 *
 * `eslint.config.js` imports this to decide severities. `tests/unit/guards/
 * a11y-ratchet.test.js` imports the same values to assert the recorded counts are the
 * real ones. Two readers, one list — a waiver cannot be true in the config and stale in
 * the guard, which is the failure mode a second copy would have.
 *
 * ═══ WHAT WAS ACTUALLY THE CASE BEFORE THIS TASK ═══
 *
 * §11.7 and task 6.27 both describe the plugin's rules as being at `warn` and needing to
 * be raised. They are not. `jsxA11y.configs.recommended` sets 31 of its 34 configured
 * rules to `error` already, and it is listed in `eslint.config.js` with no `files` key,
 * so it applies to every linted file. Measured at the point this task started:
 *
 *   | scope                       | a11y findings | severity |
 *   | --------------------------- | ------------- | -------- |
 *   | `src/components/ds/**`      | 0             | error    |
 *   | `src/pages/**`              | 38            | error    |
 *   | rest of `src/**`            | 28            | error    |
 *
 * So the page violations this task was expected to *surface* were already failing the
 * build, and `npm run lint` was already red — on those 38 plus 31 non-a11y errors
 * (`no-undef` ×21, `preserve-caught-error` ×3, `no-useless-escape` ×3, and others) that
 * have nothing to do with Requirement 18. No option available to task 6.27 makes
 * `npm run lint` green; that is a separate cleanup.
 *
 * What was genuinely missing, and is what this module adds:
 *
 *   1. The three rules `recommended` leaves OFF, two of which are Requirement 18's own
 *      criteria (see {@link A11Y_RULES_ADDED}).
 *   2. Any statement at all about which pages are held to the rule and which are not.
 *      Without one, the 38 page findings are indistinguishable from each other: a
 *      violation in a page M7 is about to rebuild reads exactly like a violation in a
 *      page nobody owns.
 *
 * ═══ WHY THE WAIVER IS A DENY-LIST AND NOT AN ALLOWLIST ═══
 *
 * The obvious shape is an allowlist of migrated pages that grows. It is the wrong way
 * round: under an allowlist the DEFAULT is unenforced, so a page added tomorrow, or a
 * page a migration task rebuilds without remembering this file, is silently exempt. That
 * is precisely "quietly skip a page".
 *
 * Inverted, the default is `error` and every exemption is a line someone had to write,
 * with a count and the task that deletes it. To skip a page you have to add it here, in a
 * diff a reviewer sees. {@link A11Y_PAGE_WAIVERS} only ever shrinks.
 *
 * ═══ WHY EVERY ENTRY IS SELF-EXPIRING ═══
 *
 * Every waived file is an **in-scope page** (Requirement 1.3's list) with a scheduled
 * rebuild task, named in its entry. When that task lands the count drops; the guard
 * requires the recorded number to move with it, and a waiver may not record `0` — a
 * cleared page has its line deleted. When the last line goes, the waiver block in
 * `eslint.config.js` and the guard's waiver section go with it.
 *
 * Pages that are NOT in scope for this spec — `Landing.jsx` (out of scope for v1, the
 * same call `no-colour-literals.budget.js` makes), and the `ExchangeManager.jsx` /
 * `Profile.jsx` / `RiskSettings.jsx` deferred routes that task 8.8 only keeps reachable
 * — get no waiver. Requirement 18 is scoped to in-scope pages, so a waiver for them
 * would be a number no task is scheduled to lower: a ratchet with no teeth, which is the
 * reasoning `no-colour-literals.test.js`'s header already applies to `index.css`. They
 * stay at the severity the preset already gave them. This task neither fixes them nor
 * pretends to; their 14 findings are pre-existing and unchanged.
 */

import jsxA11y from 'eslint-plugin-jsx-a11y-x';

const PREFIX = 'jsx-a11y-x';

/**
 * The two rules NOT raised, and why each is left where it is.
 *
 * `label-has-for` is superseded upstream by `label-has-associated-control`, which
 * `recommended` already enables. Running both makes every unlabelled control report
 * twice, and the second report says the same thing in older words.
 *
 * `prefer-tag-over-role` was tried at `error` and reports 10 findings in `ds/`. Every one
 * is on a NON-interactive semantic role, or on a documented interaction §11.7 asks for,
 * and every suggested native tag is either wrong for the content or contradicts this
 * spec's own design:
 *
 *   | finding                                     | what it wants        | why not |
 *   | ------------------------------------------- | -------------------- | ------- |
 *   | `Chart` `role="slider"` (the data cursor)    | `<input type=range>` | §11.7 asks for an arrow-key cursor. It is already a real tab stop with `onKeyDown`; a range input cannot be a transparent overlay on the recharts surface and would render a thumb. |
 *   | `ConfirmDialog`, `Drawer` `role="dialog"`   | `<dialog>`           | §17.3 puts modality behind the single-overlay registry and `useFocusTrap`. `<dialog>` brings its own top-layer modality, which is a design change, not a lint fix. |
 *   | `role="status"` ×4 (`Chart`, `DataTable`, `FilterBar`, `LoadingState`) | `<output>` | Live regions. `<output>` is form-associated and carries `name`/`for` semantics these are not. |
 *   | `StatusBadge` `NotAvailable` `role="img"`   | `<img alt=…>`        | The content is an em-dash. An `<img>` for a text glyph is worse, not better. |
 *   | `RiskIndicator` `role="progressbar"`        | `<progress>`         | Same shape as the `role="status"` rows. |
 *   | `Accordion` `role="region"`                 | `<section aria-label>` | A `<section>` with a label has role `region`; this is a spelling preference with no behavioural difference. |
 *
 * None of that is Requirement 18.1, which asks for keyboard operability. §11.7's concrete
 * instruction — no `div onClick` on an interactive element — is enforced by
 * `click-events-have-key-events`, `no-static-element-interactions` and
 * `no-noninteractive-element-interactions`, all of which the preset already has at
 * `error`. Raising a rule whose every finding would be "fixed" by making the markup worse
 * teaches contributors to reach for `eslint-disable`, which costs more than the rule buys.
 */
export const A11Y_RULES_NOT_RAISED = Object.freeze([
  `${PREFIX}/label-has-for`,
  `${PREFIX}/prefer-tag-over-role`,
]);

/**
 * The rules the preset ships OFF (`0`) that this config raises. Both are Requirement 18
 * criteria, which is the whole reason the preset alone was not enough:
 *
 *   * `control-has-associated-label` — **Requirement 18.4** verbatim: an accessible name
 *     on every interactive control.
 *   * `anchor-ambiguous-text` — link text that says "click here" names nothing, which is
 *     18.4's criterion applied to anchors.
 *
 * Listed for the reader, not consumed: they arrive through the preset with their options
 * intact and only the severity replaced (see {@link a11yRules}), which is what keeps
 * `control-has-associated-label`'s `ignoreElements` / `ignoreRoles` lists.
 */
export const A11Y_RULES_RAISED_FROM_OFF = Object.freeze([
  `${PREFIX}/control-has-associated-label`,
  `${PREFIX}/anchor-ambiguous-text`,
]);

/**
 * The rules `recommended` does not configure at all, added here.
 *
 *   * `no-aria-hidden-on-focusable` — **Requirement 18.1**. An element that is reachable
 *     by Tab and hidden from assistive technology is not operable by the trader
 *     Requirement 18 is written for; it is a focus stop that announces nothing.
 *   * `lang` — validates a `lang` attribute's value when one is written. Nothing in `ds/`
 *     writes one; included because it costs nothing and a typo'd `lang="en-Us"` on a page
 *     is silent otherwise.
 */
export const A11Y_RULES_ADDED = Object.freeze([
  `${PREFIX}/no-aria-hidden-on-focusable`,
  `${PREFIX}/lang`,
]);

/**
 * Every `jsx-a11y-x` rule at `severity`, keeping the preset's own options.
 *
 * Derived from `configs.recommended.rules` rather than retyped, for the reason
 * `eslint.config.js` gives for preferring `globals.browser` to a hand-written list: a
 * hand-maintained copy of 34 rule names drifts on the next plugin upgrade, and it drifts
 * silently. Only the severity is replaced — the options survive, so raising
 * `control-has-associated-label` from `0` to `error` keeps its `ignoreElements` /
 * `ignoreRoles` lists instead of falling back to the rule's bare defaults, which are much
 * noisier and are not what the preset's authors intended.
 *
 * @param {'error'|'warn'} severity
 * @returns {Record<string, unknown>} A flat-config `rules` object.
 */
export function a11yRules(severity) {
  const raise = (entry) => (Array.isArray(entry) ? [severity, ...entry.slice(1)] : severity);

  const fromPreset = Object.entries(jsxA11y.configs.recommended.rules)
    .filter(([name]) => !A11Y_RULES_NOT_RAISED.includes(name))
    .map(([name, entry]) => [name, raise(entry)]);

  const added = A11Y_RULES_ADDED.map((name) => [name, severity]);

  return Object.freeze(Object.fromEntries([...fromPreset, ...added]));
}

/** The globs held to `error`. `src/pages/**` minus {@link A11Y_PAGE_WAIVERS}. */
export const A11Y_ENFORCED_GLOBS = Object.freeze([
  'src/components/ds/**/*.{js,jsx,ts,tsx}',
  'src/pages/**/*.{js,jsx,ts,tsx}',
]);

/**
 * The shrinking waiver. Path relative to the terminal root, forward-slashed.
 *
 * `count` is the exact number of `jsx-a11y-x` findings in the file today. The guard
 * asserts equality in BOTH directions, the same ratchet
 * `no-colour-literals.budget.js` uses and for the same reason: under a `<=` ceiling a
 * contributor could clear four findings, leave the number where it was, and the next
 * contributor could put four back with nothing noticing. Progress has to be recorded to
 * count.
 *
 * `task` is the task that deletes the entry. There is no entry without one.
 */
export const A11Y_PAGE_WAIVERS = Object.freeze({
  // `src/pages/Portfolio.jsx` was here with 2 findings, retargeted from 16.1 to 16.2. Both
  // were `no-redundant-roles` on the allocation legend's `<ul role="list">` /
  // `<li role="listitem">`, and task 16.2 deleted that legend: the allocation's tabular
  // equivalent is a `ds/DataTable`, which is not a list and carries no role attributes at
  // all. The entry is REMOVED rather than lowered to 0, because a `0` entry is what this
  // guard forbids — a cleared page belongs at `error` with every other page nobody has
  // waived, which is what deleting the line does.
  // `src/pages/Strategies.jsx` was here: 5 findings, lowered to 3 by task 17.1 (which
  // replaced the card grid with `ds/DataTable` and with it the dashed "Create New Strategy"
  // tile's `div onClick`), then removed entirely by task 17.2's last part. The last 3 were
  // the `label-has-associated-control` cluster on the deploy modal's Execution Mode, Capital
  // and Trade Size labels — bare `<label>` elements with no `htmlFor` and no wrapped control
  // — and that modal is now a `ds/ConfirmDialog` whose every control is a `ds/Field`, which
  // renders a visible `<label htmlFor>` and offers no way not to. The entry is REMOVED rather
  // than lowered to 0, because a `0` entry is what this guard forbids: a cleared page belongs
  // at `error` with every other page nobody has waived, which is what deleting the line does.
  // The page lints at `error` from here.
  // `src/pages/SignalTrace.jsx` was here: 10 findings, cleared by task 21.4a. Eight were
  // the `label-has-associated-control` cluster on the filter grid's bare `<label>`s, and
  // the page's filters are now `ds/FilterBar` — every control a `ds/Field`, which renders a
  // visible `<label htmlFor>` and offers no way not to. The other two were the signal row's
  // `div onClick` (`click-events-have-key-events` plus `no-static-element-interactions`);
  // the rows are `ds/DataTable`'s now, and its row activation is keyboard-operable. The
  // entry is REMOVED rather than lowered to 0, because a `0` entry is what this guard
  // forbids: a cleared page belongs at `error` with every other page nobody has waived,
  // which is what deleting the line does. The page lints at `error` from here.
  // M9. Task 23.1 rebuilds the configuration flow as four `ds/Panel`s. 4 rather than 3
  // because raising `control-has-associated-label` off the preset's `0` found one more
  // here — the only page finding this task's added rules contributed.
  'src/pages/Backtester.jsx': Object.freeze({ count: 4, task: '23.1' }),
  // M9. Task 26.1 rewrites the subscription-state elements — the in-scope part of this
  // page (Requirement 1.3 scopes Marketplace to those elements only).
  'src/pages/StrategyMarketplace.jsx': Object.freeze({ count: 4, task: '26.1' }),
});
