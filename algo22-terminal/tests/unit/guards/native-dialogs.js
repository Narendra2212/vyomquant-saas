/**
 * Native browser dialog detection. Requirement 19.4, design.md §1.8, §15.1.
 *
 * Not a `*.test.js`, so vitest does not collect it — same arrangement as
 * `source-scan.js` and the `*.budget.js` files.
 *
 * ===========================================================================
 * WHO USES THIS
 * ===========================================================================
 * design.md §1.8 lists six native dialog call sites (two in `pages/Strategies.jsx`,
 * four in `pages/StrategyDetail.jsx`). Tasks 10.3 and 10.4 replace them with
 * `ds/ConfirmDialog`, and task 10.11 ships `no-native-dialogs.test.js`.
 *
 * That guard landed wider than this note first predicted, in both directions, and
 * the prediction is corrected here rather than left to mislead: it scans the whole
 * of `src/` — a dialog blocks the tab from any module, not only a page or a
 * component — and its out-of-scope allowlist is FIVE files, not the two named
 * here. `pages/TwoFA.jsx` and `components/NotificationCenter.jsx` were right;
 * `pages/ExchangeManager.jsx`, `pages/Landing.jsx` and
 * `components/admin/AdminDashboard.jsx` each hold one too, and all three were
 * inside the narrower scope as well. See `no-native-dialogs.budget.js`.
 *
 * The per-page assertion (`strategyArchive.test.jsx`) and that repo-wide guard
 * ask the same question, so they ask it with the same function: 10.11 imports
 * `findNativeDialogs` rather than writing its own pattern. Two detectors for one
 * rule is two detectors that can disagree about whether the rule is met.
 *
 * ===========================================================================
 * WHY THIS IS NOT A GREP FOR `window.confirm`
 * ===========================================================================
 * It was, and it failed on the commit that fixed the defect it was guarding.
 *
 * Task 10.3 removed both of `Strategies.jsx`'s native dialogs and left docblocks
 * recording what was there and why it had to go — `window.prompt` returns a bare
 * string with nowhere to put a label and nowhere to put a verdict, `window.confirm`
 * cannot be focus-trapped or given a review grid (Requirements 15.1, 15.2, 18.3).
 * Six such comments name the calls they replaced. A rule that reads raw file text
 * matched the prose, so the assertion failed on a file with zero real calls, and
 * the only ways to make it pass were to delete the documentation of a closed
 * defect or to weaken the rule. Both are worse than the bug.
 *
 * So: comments are stripped and quoted strings are masked (`source-scan.js`'s
 * `codeOnly`) before anything is matched, and what is matched is a *call*, not a
 * bare identifier. A string mask is not belt-and-braces here either — an error
 * message or a test fixture containing the text `window.confirm("x")` is not a
 * native dialog, and `strategyArchive.test.jsx`'s own control fixture is exactly
 * such a string.
 *
 * ===========================================================================
 * WHAT COUNTS
 * ===========================================================================
 *   window.confirm(…)  globalThis.alert(…)  self.prompt(…)   — explicit receiver
 *   confirm(…)  alert(…)  prompt(…)                          — bare global
 *   const ask = window.confirm                               — aliased, not called
 *
 * The third form is why the `window.`-prefixed alternative does not require a
 * following `(`. Restricting the whole rule to call shape would let
 * `const ask = window.confirm; ask(msg)` through, which is a weaker rule than the
 * raw-text grep this replaces — and the replacement has to be strictly stronger,
 * or it is a regression dressed as a fix.
 *
 * WHAT DOES NOT COUNT
 *   onConfirm(…)  handleConfirm(…)  promptUser(…)  — matching is case-sensitive
 *                                                   and `(?![\w$])`-terminated
 *   row.confirm(…)  dialog.alert(…)               — a method on something that is
 *                                                   not a global window object
 *   `// window.confirm cannot be focus-trapped`   — comment
 *   `throw new Error('window.confirm is gone')`   — string literal
 *
 * KNOWN BLIND SPOTS, recorded rather than patched around
 *   * `w.confirm(…)` where `w` is a local alias for `window`. Catching it needs
 *     alias analysis; a pattern that guessed would make this rule arguable, and
 *     an unarguable rule is the point.
 *   * `window['confirm'](…)`. Same reasoning as `legacy-c-budget`'s note on
 *     `C[key]`.
 *   Neither form exists in `src/` today.
 */

import { codeOnly } from './source-scan.js';

/**
 * A native dialog in code.
 *
 * Two alternatives. The first is a dialog reached through an explicit global
 * receiver, in *any* position, so aliasing is caught as well as calling. The
 * second is a bare global, which must be immediately called — a bare `confirm`
 * that is not called is far more likely to be a prop, a local, or a field name
 * than the global.
 *
 * `(?<![\w$.])` fronts both. Without it, `foo.confirm(` and `$confirm(` would read
 * as bare globals — `\b` is not sufficient, because `$` is not a word character.
 * Excluding `.` is also what stops `window.confirm(` from being counted twice,
 * once for the receiver form and once for the bare form.
 *
 * `(?![\w$])` closes the first alternative so `window.confirmArchive` and
 * `window.alerts` do not match. Matching is case-sensitive, which is what keeps
 * `onConfirm` and `handleConfirmArchive` out — this codebase's handler names all
 * capitalise.
 */
export const NATIVE_DIALOG =
  /(?<![\w$.])(?:(?:window|globalThis|self)\s*\.\s*(?:alert|confirm|prompt)(?![\w$])|(?:alert|confirm|prompt)\s*\()/g;

/**
 * Every native dialog in `source`, as `{ line, text }`, in file order.
 *
 * `text` is the matched fragment with interior whitespace collapsed, so a failure
 * message can name what it found. Line numbers refer to the file on disk:
 * `codeOnly` blanks in place, preserving both length and line breaks.
 */
export function findNativeDialogs(source) {
  const code = codeOnly(source);
  return [...code.matchAll(NATIVE_DIALOG)].map((m) => ({
    line: code.slice(0, m.index).split('\n').length,
    text: m[0].replace(/\s+/g, ''),
  }));
}

/** Whether `source` contains any native dialog. */
export const hasNativeDialog = (source) => findNativeDialogs(source).length > 0;

/** `["line 42: window.confirm("]`, for a failure message. */
export const describeNativeDialogs = (source) =>
  findNativeDialogs(source).map(({ line, text }) => `line ${line}: ${text}`);
