/**
 * The `no-native-dialogs` budget — vyomquant-ui-redesign task 10.11.
 * Requirements 18.3, 19.4. design.md §1.8, §1.9, §15.1, §17.2.
 *
 * ---------------------------------------------------------------------------
 * WHAT THIS FILE IS
 * ---------------------------------------------------------------------------
 * A checked-in count, per file, of how many native browser dialogs — a call to
 * or an alias of `window.confirm` / `window.alert` / `window.prompt`, in any of
 * the forms `guards/native-dialogs.js` matches — that file is still allowed to
 * contain. `no-native-dialogs.test.js` measures the real counts and asserts each
 * file matches its entry **exactly**:
 *
 *   * over budget  -> a native dialog was introduced. Fix it.
 *   * under budget -> a dialog was removed and this file was not updated.
 *                     Lower the number here in the same commit.
 *
 * The second half is the point, and it is the same argument
 * `no-colour-literals.budget.js` makes at length: a `<=` assertion lets a
 * contributor remove a dialog and leave the headroom open, so the next
 * contributor can put one back without CI noticing. Progress has to be
 * *recorded* to count. Every entry below is 1 or 0, so "headroom" here means the
 * difference between "this file is allowed one dialog" and "this file is allowed
 * none" — which is exactly the distinction Requirement 18.3 cares about.
 *
 * ---------------------------------------------------------------------------
 * HOW TO CHANGE IT
 * ---------------------------------------------------------------------------
 * Lowering an out-of-scope number to `0` is what happens when a deferred page is
 * eventually migrated; delete the entry only when the file itself is deleted, so
 * that a live file at `0` stays held at `0`. Raising a number, or adding an entry
 * to the out-of-scope group, means a new native dialog entered the tree and needs
 * a reason in the PR description.
 *
 * Adding an entry to the **in-scope** group is only ever legal at `0`. See below.
 *
 * ---------------------------------------------------------------------------
 * SEEDED FROM THE TREE ON THE DAY TASK 10.11 LANDED
 * ---------------------------------------------------------------------------
 * 204 non-test JavaScript files under `src/`, five of which carry exactly one
 * native dialog each. Measured with `findNativeDialogs`, not estimated — and
 * measured through the same function the two per-page suites
 * (`strategyArchive.test.jsx`, `strategyDetail.test.jsx`) already call, so a
 * number here and a number there cannot disagree about what a dialog is.
 *
 * **No in-scope page carries one.** All eleven pages with an M6-M9 page task
 * measure `0`, so the in-scope group below is all zeroes and the test asserts
 * that set is empty rather than committing the assertion skipped. Tasks 10.3 and
 * 10.4 are why: 10.3 took `pages/Strategies.jsx`'s `window.confirm` and
 * `window.prompt`, 10.4 took `pages/StrategyDetail.jsx`'s four `window.confirm`
 * calls and the two bare `alert(` calls in its marketplace tab that §1.8's table
 * never listed.
 *
 * ---------------------------------------------------------------------------
 * THE ALLOWLIST IS FIVE FILES, NOT THE TWO THE PLAN PREDICTED
 * ---------------------------------------------------------------------------
 * tasks.md's ordering-hazard note (hazard 3) and `guards/native-dialogs.js`'s
 * header both say this guard ships "an out-of-scope allowlist of exactly
 * `pages/TwoFA.jsx` and `components/NotificationCenter.jsx`". The tree holds
 * five. The prediction was not wrong about those two; it was incomplete, and it
 * was already incomplete for the narrower `src/pages/**` + `src/components/**`
 * scope it was written against — `pages/ExchangeManager.jsx`, `pages/Landing.jsx`
 * and `components/admin/AdminDashboard.jsx` each hold one too, and all three
 * were reached by that scope as well.
 *
 * What matters is that every one of the five is a surface design.md §17.2 defers
 * by name: Auth/2FA, the Notification Center page, Exchange Manager, the Landing
 * page, and the Admin dashboard. So the finding is "the plan's count was low",
 * not "a page in scope is carrying a dialog". Recorded here rather than silently
 * widened, because an allowlist that grows without comment is how a guard stops
 * meaning anything.
 *
 * Three of the five are **bare** calls — `confirm(…)` and `alert(…)` with no
 * `window.` receiver. A guard written as a grep for `window.confirm` would have
 * found two of five and reported the tree 60% cleaner than it is. That is the
 * second reason the detector is shared rather than re-written per caller.
 *
 * ---------------------------------------------------------------------------
 * THE TWO GROUPS
 * ---------------------------------------------------------------------------
 * The split is `no-colour-literals.budget.js`'s, established at task 27.3, and it
 * is copied deliberately:
 *
 *   IN_SCOPE     — the eleven pages design.md gives a page task in M6-M9, plus
 *                  the two `ds/` primitives §1.8 and §1.9 name as the
 *                  replacements. The test asserts EVERY entry here is `0` AND
 *                  that no in-scope file measures a dialog. There is no headroom
 *                  to hold; the number is the constant zero.
 *   OUT_OF_SCOPE — everything else. Budgeted so it cannot grow, with no promise
 *                  in this spec that it shrinks.
 *
 * `NATIVE_DIALOG_BUDGET` is the two spread together and is what the ratchet
 * assertions read, so both groups are held to their recorded number; the
 * emptiness assertion is added on top of that, not in place of it.
 *
 * The consequence worth stating is what it forbids: an in-scope entry cannot be
 * raised above `0`, not even with a reason in the PR, and a new in-scope page's
 * entry can only be seeded at `0`. A page cannot enter this tree carrying a
 * native dialog. Requirement 18.3 is explicit that these block the tab, so
 * "temporarily" is not available as an argument.
 */

/**
 * The eleven pages design.md gives a page task in M6-M9, plus the two primitives
 * that exist to replace a native dialog. Every entry here must be `0`.
 *
 * Paths are relative to `src/`, forward-slashed, matching the spec's notation.
 *
 * Every one of these entries is `0` as it is seeded, which is the whole reason
 * they are written down. A `0` records that the file is clean and holds it there;
 * an in-scope file with NO entry is a file whose cleanliness nothing is holding.
 * That is the same reason `no-colour-literals.budget.js` keeps
 * `pages/Dashboard.jsx: 0` after task 19.2 cleared it.
 */
export const IN_SCOPE_NATIVE_DIALOG_BUDGET = Object.freeze({
  // -- The eleven M6-M9 page tasks -------------------------------------------
  // Clean as this guard is seeded. Two of the eleven were cleaned by this
  // milestone rather than having always been clean:
  //
  //   * `pages/Strategies.jsx` — task 10.3 replaced §1.8's two calls,
  //     `window.confirm(archiveConfirmMessage(name))` and
  //     `window.prompt("Enter new strategy name:", …)`, with two
  //     `ds/ConfirmDialog`s. The page still names both calls in six docblocks
  //     explaining why they had to go, which is precisely why this guard strips
  //     comments before counting — see the test's docblock.
  //   * `pages/StrategyDetail.jsx` — task 10.4 replaced §1.8's four
  //     `window.confirm` calls (deploy, delete, restore version, deploy version)
  //     and the two bare `alert(…)` calls in its marketplace tab that §1.8 never
  //     recorded. Those two were found by `findNativeDialogs`, not by the table.
  //
  // The other nine entered the guard clean. They are listed anyway, for the
  // reason in this group's header.
  'pages/Dashboard.jsx': 0,
  'pages/Portfolio.jsx': 0,
  'pages/Strategies.jsx': 0,
  'pages/TradeHistory.jsx': 0,
  'pages/LiveTrading.jsx': 0,
  'pages/SignalTrace.jsx': 0,
  'pages/Backtester.jsx': 0,
  'pages/StrategyBuilder.jsx': 0,
  'pages/PaperTrading.jsx': 0,
  'pages/StrategyMarketplace.jsx': 0,
  'pages/StrategyDetail.jsx': 0,

  // -- The two primitives that replaced them --------------------------------
  // §1.8 retires `window.confirm` in favour of `ds/ConfirmDialog`; §1.9's
  // Requirement 19.4 clean-up and task 10.4 retire `window.alert` in favour of
  // `ds/Alert` and the `window.showToast` transport `AppShell` installs.
  //
  // These are in the in-scope group because a native dialog inside either would
  // be the most expensive place in the tree for one: a `ConfirmDialog` that fell
  // back to `window.confirm` for some edge case would defeat the focus trap, the
  // accessible name, the review grid and the single-overlay claim on every page
  // at once, and it would do it without any page's own budget moving. Both
  // measure `0`; the entries hold them there.
  'components/ds/ConfirmDialog.jsx': 0,
  'components/ds/Alert.jsx': 0,
});

/**
 * Everything else. Budgeted so it cannot grow; no promise in this spec that it
 * shrinks, and no emptiness assertion over it.
 *
 * All five entries are pages and components design.md §17.2 defers by name, and
 * all five hold exactly one dialog. Each note below records the call, its line as
 * seeded, and the interaction it guards — so that when one of these surfaces is
 * eventually migrated, the person doing it knows what they are replacing without
 * re-deriving it.
 */
export const OUT_OF_SCOPE_NATIVE_DIALOG_BUDGET = Object.freeze({
  // -- Deferred pages (§17.2) ------------------------------------------------
  // `window.confirm("Are you sure you want to reset your MFA configuration?")`
  // at line 202, guarding `handleResetFactor`. Auth/2FA is deferred by §17.2, and
  // this is the one file tasks.md's hazard note named correctly. Destructive and
  // security-relevant, so it is the entry most worth migrating first if §17.2
  // ever reopens — a reset that cannot be focus-trapped or read out is exactly
  // Requirement 18.3's complaint.
  'pages/TwoFA.jsx': 1,
  // Bare `confirm(\`… disconnect ${exchangeName.toUpperCase()}? This action
  // cannot be undone.\`)` at line 233. No `window.` receiver — this is one of the
  // three the plan's `window.confirm` grep would have missed. Exchange Manager is
  // deferred by §17.2.
  'pages/ExchangeManager.jsx': 1,
  // Bare `alert("Demo request submitted! …")` at line 604, in the demo-modal
  // submit handler. The landing page is out of scope for v1 throughout this spec
  // (§17.2, and `index.css`'s own header says the same of the landing
  // `@utility` compositions).
  'pages/Landing.jsx': 1,

  // -- Deferred components (§17.2) ------------------------------------------
  // `window.confirm("Are you sure you want to clear all notifications?")` at
  // line 148, guarding `deleteAllNotifications`. The Notification Center page is
  // deferred by §17.2; the other file tasks.md's hazard note named.
  'components/NotificationCenter.jsx': 1,
  // Bare `confirm('Delete this entry permanently?')` at line 71, guarding the
  // waitlist delete. The Admin dashboard is deferred by §17.2. Bare form again.
  'components/admin/AdminDashboard.jsx': 1,
});

/**
 * The whole budget, and what the ratchet assertions in `no-native-dialogs.test.js`
 * read. Each file has exactly one recorded number and fails in both directions
 * against it, in both groups. The split adds one thing on top: that the in-scope
 * half is all zeroes and measures all zeroes.
 *
 * Spread rather than hand-maintained, so the flat map cannot fall out of step
 * with the groups. The test asserts the two groups are disjoint, which is what
 * makes the spread lossless — an entry declared in both would silently take the
 * out-of-scope number here, and that is the one way an in-scope file could slip
 * past the emptiness rule.
 */
export const NATIVE_DIALOG_BUDGET = Object.freeze({
  ...IN_SCOPE_NATIVE_DIALOG_BUDGET,
  ...OUT_OF_SCOPE_NATIVE_DIALOG_BUDGET,
});

/**
 * The token layer, excluded from the scan.
 *
 * Only one member of it is JavaScript: `design/tokens.js`, which is generated by
 * `scripts/gen-tokens.mjs` and carries an `AUTO-GENERATED … DO NOT EDIT` header.
 * This guard governs code a person wrote, and a generated file is not that — a
 * finding in it would have to be fixed in the generator, which this guard does
 * not read.
 *
 * `styles/tokens.css` and `index.css` are the other two members
 * `no-colour-literals.budget.js` names. They are CSS, so they are outside this
 * guard's extension set already and are not repeated here; the test asserts the
 * scan holds no `.css` file at all, so that omission is checked rather than
 * assumed.
 *
 * Nothing is being hidden by this: `design/tokens.js` measures `0`, so the
 * exclusion moves no number. It is a statement about what this guard governs, not
 * a workaround for something it found.
 */
export const TOKEN_LAYER_FILES = Object.freeze(['design/tokens.js']);
