/**
 * `no-native-dialogs` — vyomquant-ui-redesign task 10.11.
 * Requirements 18.3, 19.4. design.md §1.8, §1.9, §15.1, §17.2.
 *
 * ===========================================================================
 * WHAT THIS GUARD IS FOR
 * ===========================================================================
 * Requirement 18.3: a destructive action is confirmed in an overlay the app
 * owns — focus-trapped, named, reviewable — not by a native browser dialog that
 * blocks the tab and renders whatever the browser feels like rendering.
 *
 * design.md §1.8 tabulated six call sites: two in `pages/Strategies.jsx`
 * (`window.confirm` for archive, `window.prompt` for rename) and four in
 * `pages/StrategyDetail.jsx` (deploy, delete, restore version, deploy version).
 * Tasks 10.3 and 10.4 replaced all six with `ds/ConfirmDialog`, and 10.4 also
 * took two bare `alert(…)` calls in the marketplace tab that the table never
 * listed. This guard is what stops the seventh, in any file, without anyone
 * having to predict which file.
 *
 * It is not a ratchet on a migration in flight. Its resting state is zero
 * everywhere in scope, and the budget beside it is an allowlist of the handful
 * of deferred surfaces that still carry one.
 *
 * ===========================================================================
 * IT DOES NOT WRITE ITS OWN DETECTOR
 * ===========================================================================
 * `guards/native-dialogs.js` already answers "is this a native dialog", and
 * `strategyArchive.test.jsx` and `strategyDetail.test.jsx` already ask it. This
 * file imports `findNativeDialogs` and adds only scope and arithmetic on top.
 * Two detectors for one rule is two detectors that can disagree about whether
 * the rule is met, and the disagreement surfaces as a guard that reads clean
 * while a per-page suite fails, or the reverse.
 *
 * What that buys, in this tree, is measurable: three of the five files in the
 * allowlist call the **bare** global — `confirm(…)`, `alert(…)` with no
 * `window.` receiver. A guard written as a grep for `window.confirm` would have
 * found two of five and reported the tree 60% cleaner than it is.
 *
 * ===========================================================================
 * COMMENTS ARE STRIPPED AND STRINGS ARE MASKED, AND THAT IS LOAD-BEARING
 * ===========================================================================
 * The detector matches `codeOnly(source)` — comments blanked, string spans
 * masked, both length-preserving. This guard would be actively harmful without
 * it, and not hypothetically:
 *
 *   * `pages/Strategies.jsx` carries six docblocks naming the two calls task
 *     10.3 removed and saying why they had to go — `window.prompt` returns a
 *     bare string with nowhere to put a label or a verdict, `window.confirm`
 *     cannot be focus-trapped or given a review grid.
 *   * `pages/StrategyDetail.jsx` records the same for its four, plus the two
 *     marketplace `alert(…)` calls.
 *
 * Both files measure zero and both still spell the calls out in prose. A guard
 * that read raw text would fail on the commits that FIXED the defect, and the
 * two cheapest ways to make it pass would be to delete the explanation of a
 * closed defect or to weaken the rule. `strips the prose that documents a
 * removal` asserts this against the real tree rather than against a fixture, so
 * it cannot rot into a claim.
 *
 * A masked string is the same argument one step out: `throw new Error('window.
 * confirm is gone')` is not a native dialog, and `strategyArchive.test.jsx`'s
 * control fixture is exactly such a string.
 *
 * ===========================================================================
 * WHAT IS OUT OF SCOPE — STATED, BECAUSE THE GAP IS REAL
 * ===========================================================================
 * `window.print`, `window.open` and `console.*` are NOT measured here. Only the
 * three modal dialogs Requirement 18.3 is about — `confirm`, `alert`, `prompt` —
 * in the forms `native-dialogs.js` enumerates.
 *
 * The consequence is worth naming rather than leaving for someone to discover:
 * design.md §1.9 found two placeholder defects, and **the second one was a
 * `window.open`** — `components/Sidebar.jsx`'s `docs` entry, which pointed at an
 * external target. This guard would not have caught it. Task 8.6's rebuild of
 * the sidebar against `shell/navigation.js` is what resolved it, and
 * `no-placeholders.test.js` is the guard that holds that class of defect. Do not
 * read a clean run here as a statement about dead links, new windows, or
 * leftover logging.
 *
 * `native-dialogs.js` records two further blind spots inside the rule itself —
 * `w.confirm(…)` through a local alias for `window`, and `window['confirm'](…)`.
 * Neither form exists in `src/` today, and catching either needs analysis rather
 * than matching.
 *
 * ===========================================================================
 * SCOPE: THE WHOLE OF src/, NOT THREE ROOTS
 * ===========================================================================
 * `no-colour-literals`, `legacy-c-budget` and `no-placeholders` scan
 * `pages/`, `components/` and `lib/`, because presentation debt only exists
 * where something is presented. A native dialog is different: it is reachable
 * from anywhere that runs in a browser, and it blocks the tab from wherever it
 * is called. A `confirm(…)` inside an API wrapper, a hook or a store is the same
 * defect Requirement 18.3 names, with the added property that no page's budget
 * would move when it appeared. So the scan root is `src/` itself — 204 non-test
 * JavaScript files as this lands, against the ~136 the three-root scans reach.
 *
 * Excluded, and asserted below so a reader sees the decision rather than
 * inferring it from an absence:
 *
 *   * THE TOKEN LAYER — `TOKEN_LAYER_FILES`, which for this guard is the single
 *     member of it that is JavaScript: `design/tokens.js`, generated by
 *     `scripts/gen-tokens.mjs` under a DO NOT EDIT header. A finding there would
 *     have to be fixed in the generator, which this guard does not read. It
 *     measures zero, so the exclusion moves no number. `styles/tokens.css` and
 *     `index.css` are CSS and fall outside the extension set; `holds no
 *     stylesheet` asserts that rather than assuming it.
 *   * TESTS — `__tests__/`, `*.test.*`, `*.spec.*`, via `isTestFile`. A test that
 *     asserts a dialog is absent has to name it to do so, and this file names all
 *     three several times.
 *
 * ===========================================================================
 * TWO DIRECTIONS, AND A `0` THAT IS NOT DELETED
 * ===========================================================================
 * Every scanned file must measure EXACTLY its budget entry: over budget is a new
 * dialog, under budget is a removal that was not written down, and headroom left
 * open after a clean-up is headroom the next dialog creeps into. Unlisted files
 * default to zero and fail through `unbudgeted`.
 *
 * One deliberate divergence from `no-placeholders`, whose budget deletes an
 * entry that reaches `0`: this budget's header says the opposite, and this file
 * follows the budget. An out-of-scope entry lowered to `0` by a migration STAYS,
 * so that a live file at `0` is held at `0` rather than being returned to the
 * unlisted default. Entries leave only when the file does. `names only files that
 * still exist` is what keeps that from accumulating ghosts, so there is no
 * emptied-entry assertion here.
 */

import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, it, expect } from 'vitest';

import { describeNativeDialogs, findNativeDialogs, hasNativeDialog } from './native-dialogs.js';
import {
  IN_SCOPE_NATIVE_DIALOG_BUDGET,
  NATIVE_DIALOG_BUDGET,
  OUT_OF_SCOPE_NATIVE_DIALOG_BUDGET,
  TOKEN_LAYER_FILES,
} from './no-native-dialogs.budget.js';
import { SRC, collect, isTestFile, list, relToSrc } from './source-scan.js';

const BUDGET_FILE = 'tests/unit/guards/no-native-dialogs.budget.js';

/**
 * design.md's M6-M9 page tasks — the same eleven names `no-colour-literals` and
 * `no-placeholders` enumerate, and for the same reason: "in-scope page" means this
 * set, not whatever happens to be listed in a budget today.
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

/** The two primitives that exist to replace a native dialog (§1.8, §1.9). */
const REPLACEMENT_PRIMITIVES = Object.freeze([
  'components/ds/ConfirmDialog.jsx',
  'components/ds/Alert.jsx',
]);

/** A dialog can be called from any module that runs in a browser. `.css` carries none. */
const SCANNED_EXTENSIONS = Object.freeze(['.js', '.jsx', '.ts', '.tsx']);

/**
 * The five deferred surfaces that really hold one dialog each, with the form the
 * detector reports. Hard-coded rather than read from the budget: these are the
 * FIXED POINTS that prove the scan and the detector are both working, and a fixed
 * point taken from the thing under test proves nothing.
 *
 * `text` is the matched fragment with whitespace removed. The receiver form has no
 * trailing `(` because `NATIVE_DIALOG`'s first alternative also catches an alias
 * that is never called; the bare form must be called, so it keeps its `(`.
 *
 * Lines are deliberately NOT pinned — they move when the file above them is edited,
 * and a fixed point that re-bases on every unrelated commit is a fixed point nobody
 * trusts. The budget records them in prose for the person doing the migration.
 */
const KNOWN_DIALOGS = Object.freeze([
  { relative: 'pages/TwoFA.jsx', texts: ['window.confirm'] },
  { relative: 'pages/ExchangeManager.jsx', texts: ['confirm('] },
  { relative: 'pages/Landing.jsx', texts: ['alert('] },
  { relative: 'components/NotificationCenter.jsx', texts: ['window.confirm'] },
  { relative: 'components/admin/AdminDashboard.jsx', texts: ['confirm('] },
]);

// ---------------------------------------------------------------------------
// Counting
// ---------------------------------------------------------------------------

/** How many native dialogs a source file contains, through the shared detector. */
export function countNativeDialogs(source) {
  return findNativeDialogs(source).length;
}

/** `{ relative, count, found }` for one file. */
function measure(relative, source) {
  const found = findNativeDialogs(source);
  return { relative, found, count: found.length };
}

/** One report line: the file, the total, and where each call is. */
const describeFinding = (f) =>
  `${f.relative} — ${f.count} (${f.found.map(({ line, text }) => `line ${line}: ${text}`).join('; ')})`;

// ---------------------------------------------------------------------------
// The report builders
// ---------------------------------------------------------------------------
//
// Pure functions of (findings, budget) rather than closures over the real scan, so
// "the guard would fail on a planted dialog" is a test rather than a claim. The
// liveness section hands each of them a synthetic finding and asserts it comes back
// reported. Every assertion in the two sections below compares one of these against
// `[]`, and a list that is always empty passes whatever the tree holds.

/** Files carrying a dialog that the budget does not name. */
export const unbudgeted = (findings, budget) =>
  findings.filter((f) => f.count > 0 && !(f.relative in budget)).map(describeFinding);

/** Files carrying more dialogs than their entry allows. */
export const overBudget = (findings, budget) =>
  findings
    .filter((f) => f.relative in budget && f.count > budget[f.relative])
    .map((f) => `${describeFinding(f)} — budget ${budget[f.relative]}`);

/** Files carrying fewer than their entry allows: progress that was not written down. */
export const underBudget = (findings, budget) =>
  findings
    .filter((f) => f.relative in budget && f.count < budget[f.relative])
    .map(
      (f) => `${f.relative} — lower the committed budget from ${budget[f.relative]} to ${f.count}`,
    );

/** In-scope files carrying a dialog, whatever their entry says. */
export const dirtyInScope = (findings, inScope) =>
  findings.filter((f) => f.relative in inScope && f.count > 0).map(describeFinding);

// ---------------------------------------------------------------------------
// Scanning
// ---------------------------------------------------------------------------

function scan() {
  const excluded = new Set(TOKEN_LAYER_FILES);
  const files = [];
  for (const full of collect(SRC, SCANNED_EXTENSIONS)) {
    const relative = relToSrc(full);
    if (isTestFile(relative) || excluded.has(relative)) continue;
    files.push(measure(relative, readFileSync(full, 'utf8')));
  }
  return files.sort((a, b) => a.relative.localeCompare(b.relative));
}

const SCANNED = scan();

/** The raw bytes of one scanned file, for the comment-stripping assertions. */
const rawSource = (relative) => readFileSync(path.join(SRC, relative), 'utf8');

// ---------------------------------------------------------------------------
// 1. The detector, as this guard uses it
// ---------------------------------------------------------------------------

describe('no-native-dialogs: what counts as a dialog', () => {
  it('counts the three dialogs through an explicit global receiver', () => {
    expect(countNativeDialogs('if (window.confirm("Archive?")) archive();')).toBe(1);
    expect(countNativeDialogs('window.alert("Saved");')).toBe(1);
    expect(countNativeDialogs('const name = window.prompt("Rename", current);')).toBe(1);
    expect(countNativeDialogs('globalThis.alert("x"); self.confirm("y");')).toBe(2);
  });

  it('counts the bare global, which is how three of the five real ones are written', () => {
    // A grep for `window.confirm` finds two of the five files in the allowlist.
    expect(countNativeDialogs('if (confirm(`Disconnect ${name}?`)) drop();')).toBe(1);
    expect(countNativeDialogs('alert("Demo request submitted!");')).toBe(1);
    expect(countNativeDialogs('prompt("Enter new strategy name:", current);')).toBe(1);
  });

  it('counts an alias that is never called', () => {
    // Restricting the whole rule to call shape would let this through, which would be
    // weaker than the raw-text grep it replaces.
    expect(countNativeDialogs('const ask = window.confirm; ask(msg);')).toBe(1);
    expect(countNativeDialogs('const { confirm } = window;')).toBe(0);
  });

  it('counts `window.confirm(` once, not once per alternative', () => {
    expect(findNativeDialogs('window.confirm("x");')).toEqual([
      { line: 1, text: 'window.confirm' },
    ]);
  });

  it('does not fire on a handler or a method that merely reads like one', () => {
    expect(countNativeDialogs('<Row onConfirm={handleConfirm} />')).toBe(0);
    expect(countNativeDialogs('const ok = await confirmArchive(id);')).toBe(0);
    expect(countNativeDialogs('promptUser({ label: "Name" });')).toBe(0);
    expect(countNativeDialogs('row.confirm(id); dialog.alert(msg);')).toBe(0);
    expect(countNativeDialogs('window.confirmArchive(id); window.alerts.push(x);')).toBe(0);
  });

  it('ignores a dialog named in a comment, which is how a removal is documented', () => {
    // The whole reason the detector reads `codeOnly`. See the header, and see
    // `strips the prose that documents a removal` for the same thing on the real tree.
    expect(
      countNativeDialogs(
        [
          '// Task 10.3 replaced window.confirm(archiveConfirmMessage(name)) with',
          '// ds/ConfirmDialog: window.confirm cannot be focus-trapped (Requirement 18.3).',
        ].join('\n'),
      ),
    ).toBe(0);
    expect(
      countNativeDialogs('/* window.prompt("Rename") had nowhere to put a verdict. */'),
    ).toBe(0);
  });

  it('ignores a dialog named inside a string, and still sees one beside it', () => {
    expect(countNativeDialogs('throw new Error("window.confirm is gone");')).toBe(0);
    expect(countNativeDialogs("const fixture = 'if (confirm(\"x\")) go();';")).toBe(0);
    expect(countNativeDialogs('log("window.confirm is gone"); window.confirm("really?");')).toBe(1);
  });

  it('reports the line the call is really on, and names it', () => {
    const source = ['// a comment', '', 'if (window.confirm("Delete?")) remove();'].join('\n');
    expect(hasNativeDialog(source)).toBe(true);
    expect(describeNativeDialogs(source)).toEqual(['line 3: window.confirm']);
  });
});

// ---------------------------------------------------------------------------
// 2. Scope
// ---------------------------------------------------------------------------

describe('no-native-dialogs: scope', () => {
  it('scans the whole of src/, and only src/', () => {
    // A dialog blocks the tab from wherever it is called, so the root is `src/` rather
    // than the three presentation roots the sibling guards use. `SRC` is
    // `<repo>/algo22-terminal/src`; this assertion is what keeps the guard from ever
    // reaching `tests/`, where all three dialog names are spelled out repeatedly — and
    // where the cheapest fix would be to delete the explanation.
    expect(path.basename(SRC)).toBe('src');
    expect(existsSync(SRC)).toBe(true);
    for (const { relative } of SCANNED) {
      expect(relative.startsWith('..'), `${relative} escapes src/`).toBe(false);
      expect(path.isAbsolute(relative), `${relative} is not relative to src/`).toBe(false);
    }
    expect(SCANNED.some((f) => f.relative.includes('tests/'))).toBe(false);
  });

  it('reaches past the presentation roots, which is the point of the wider scope', () => {
    const roots = new Set(SCANNED.map((f) => f.relative.split('/')[0]));
    for (const root of ['pages', 'components']) {
      expect(roots.has(root), `src/${root} is not being scanned`).toBe(true);
    }
    expect(
      [...roots].some((root) => !['pages', 'components', 'lib'].includes(root)),
      'the scan reaches nothing outside the three presentation roots',
    ).toBe(true);
  });

  it('holds only JavaScript, and no stylesheet', () => {
    // The budget's claim that `styles/tokens.css` and `index.css` need no exclusion
    // because they are outside the extension set, checked rather than assumed.
    for (const { relative } of SCANNED) {
      expect(
        SCANNED_EXTENSIONS.some((ext) => relative.endsWith(ext)),
        `${relative} is not one of ${SCANNED_EXTENSIONS.join(', ')}`,
      ).toBe(true);
    }
    expect(SCANNED.some((f) => f.relative.endsWith('.css'))).toBe(false);
  });

  it('leaves the generated token module out, on purpose and on the record', () => {
    expect(TOKEN_LAYER_FILES).toEqual(['design/tokens.js']);
    for (const relative of TOKEN_LAYER_FILES) {
      expect(existsSync(path.join(SRC, relative)), `${relative} is missing`).toBe(true);
      expect(SCANNED.some((f) => f.relative === relative)).toBe(false);
      expect(Object.keys(NATIVE_DIALOG_BUDGET)).not.toContain(relative);
      // Nothing is hidden by the exclusion: it measures zero either way.
      expect(countNativeDialogs(rawSource(relative))).toBe(0);
    }
  });

  it('leaves test files out', () => {
    expect(SCANNED.some((f) => isTestFile(f.relative))).toBe(false);
    expect(isTestFile('pages/__tests__/strategyArchive.test.jsx')).toBe(true);
    expect(isTestFile('components/ds/ConfirmDialog.test.jsx')).toBe(true);
    expect(isTestFile('pages/Strategies.jsx')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 3. Non-vacuity — the guard is live
// ---------------------------------------------------------------------------

describe('no-native-dialogs: the guard is live, not vacuous', () => {
  it('reads a non-trivial number of files', () => {
    // A scan that reached nothing would pass every assertion below in silence.
    //
    // THIS IS A LIVENESS TRIPWIRE OVER THE FILE COUNT, NOT A FLOOR ON THE THING BEING
    // MEASURED. There is no minimum number of dialogs asserted anywhere in this file:
    // the dialog count is meant to fall to zero, and `legacy-c-budget`'s magnitude floor
    // had to be re-based twice for exactly that reason. File count is not a shrinking
    // quantity, but files do leave the tree — `components/ui-legacy/primitives.jsx` goes
    // at task 27.2 — so 150 sits well below the 204 the scan reads today and far above
    // the handful-of-files case it exists to catch.
    expect(SCANNED.length).toBeGreaterThan(150);
    for (const relative of ['pages/Strategies.jsx', 'components/ds/ConfirmDialog.jsx']) {
      expect(
        SCANNED.some((f) => f.relative === relative),
        `${relative} was not reached by the scan`,
      ).toBe(true);
    }
  });

  it('finds the dialogs that are really in the tree', () => {
    // THE FIXED POINTS. Five deferred surfaces, one dialog each, asserted against
    // hard-coded expectations rather than against the budget. If path resolution broke, or
    // the detector stopped matching, every count would be 0 and only the under-budget
    // direction would object — so real files that MUST measure non-zero are what prove
    // the scan ran. Three of the five are bare calls, so this also pins the claim that
    // a `window.`-only rule would miss most of them.
    for (const { relative, texts } of KNOWN_DIALOGS) {
      const found = SCANNED.find((f) => f.relative === relative);
      expect(found, `${relative} was not reached by the scan`).toBeDefined();
      expect(found.count, `${relative} no longer measures a dialog`).toBe(1);
      expect(found.found.map((d) => d.text)).toEqual(texts);
    }
    expect(KNOWN_DIALOGS.filter(({ texts }) => !texts[0].startsWith('window.'))).toHaveLength(3);
  });

  it('strips the prose that documents a removal', () => {
    // The header's argument, against the real tree. These in-scope pages still spell the
    // calls tasks 10.3 and 10.4 removed — in docblocks explaining why they had to go — and
    // they measure zero. A raw-text rule would fail here, and the cheapest way to make it
    // pass would be to delete the record of a closed defect.
    const documented = SCANNED.filter(
      (f) =>
        f.relative in IN_SCOPE_NATIVE_DIALOG_BUDGET
        && /window\.(?:confirm|alert|prompt)/.test(rawSource(f.relative)),
    );

    expect(
      documented.map((f) => f.relative),
      'no in-scope page documents a removed dialog in prose any more, so this assertion\n'
        + 'has stopped proving that comments are stripped. Find another fixed point before\n'
        + 'relaxing it.',
    ).toContain('pages/Strategies.jsx');
    for (const f of documented) {
      expect(f.count, `${f.relative} matched its own documentation`).toBe(0);
    }
  });

  it('would report a planted dialog on an in-scope page', () => {
    const planted = measure(
      'pages/Strategies.jsx',
      ['export function archive(name) {', '  if (window.confirm(`Archive ${name}?`)) drop(name);', '}'].join(
        '\n',
      ),
    );

    expect(planted.count).toBe(1);
    expect(planted.found).toEqual([{ line: 2, text: 'window.confirm' }]);

    // Every gate that guards an in-scope page reports it.
    expect(dirtyInScope([planted], IN_SCOPE_NATIVE_DIALOG_BUDGET)).toHaveLength(1);
    expect(overBudget([planted], NATIVE_DIALOG_BUDGET)).toHaveLength(1);
    expect(unbudgeted([planted], {})).toHaveLength(1);
    // And the report names the file and the call site, not just a count.
    expect(dirtyInScope([planted], IN_SCOPE_NATIVE_DIALOG_BUDGET)[0]).toContain(
      'pages/Strategies.jsx',
    );
    expect(dirtyInScope([planted], IN_SCOPE_NATIVE_DIALOG_BUDGET)[0]).toContain(
      'line 2: window.confirm',
    );
  });

  it('would report a dialog in a file no budget names', () => {
    const planted = measure('hooks/useStrategies.js', 'if (!confirm("Discard?")) return;');

    expect(planted.count).toBe(1);
    expect(unbudgeted([planted], NATIVE_DIALOG_BUDGET)).toHaveLength(1);
    // The wider scope earns its keep here: no page's budget would move for this file.
    expect(overBudget([planted], NATIVE_DIALOG_BUDGET)).toEqual([]);
  });

  it('would report a cleaned file whose entry was left behind', () => {
    const cleaned = measure('pages/TwoFA.jsx', '<ConfirmDialog onConfirm={resetFactor} />');

    expect(cleaned.count).toBe(0);
    expect(underBudget([cleaned], NATIVE_DIALOG_BUDGET)).toHaveLength(1);
    expect(underBudget([cleaned], NATIVE_DIALOG_BUDGET)[0]).toContain('from 1 to 0');
  });
});

// ---------------------------------------------------------------------------
// 4. The budget is an allowlist, asserted exactly
// ---------------------------------------------------------------------------

describe('no-native-dialogs: the budget', () => {
  it('is well formed and frozen', () => {
    for (const map of [
      IN_SCOPE_NATIVE_DIALOG_BUDGET,
      OUT_OF_SCOPE_NATIVE_DIALOG_BUDGET,
      NATIVE_DIALOG_BUDGET,
    ]) {
      expect(Object.isFrozen(map)).toBe(true);
    }
    for (const [relative, budget] of Object.entries(NATIVE_DIALOG_BUDGET)) {
      expect(Number.isInteger(budget), `${relative}: ${budget} is not an integer`).toBe(true);
      expect(budget, `${relative}: ${budget} is negative`).toBeGreaterThanOrEqual(0);
      expect(relative, `${relative} must be relative to src/ with forward slashes`).toMatch(
        /^[\w.-]+(?:\/[\w.-]+)*$/,
      );
    }
  });

  it('names only files that still exist', () => {
    // The one thing holding the allowlist down, since an entry that reaches 0 is KEPT
    // here rather than deleted. An entry leaves when its file does.
    const gone = Object.keys(NATIVE_DIALOG_BUDGET).filter(
      (relative) => !existsSync(path.join(SRC, relative)),
    );
    expect(
      gone,
      'These budget entries name files that are no longer in src/. A deleted file takes its\n'
        + `entry with it — remove them from ${BUDGET_FILE}:\n${list(gone)}`,
    ).toEqual([]);
  });

  it('has an entry for every file it claims to track, and no strays', () => {
    const scannedNames = new Set(SCANNED.map((f) => f.relative));
    const strays = Object.keys(NATIVE_DIALOG_BUDGET).filter(
      (relative) => !scannedNames.has(relative),
    );
    expect(
      strays,
      "These budget entries name files that exist but are outside this guard's scan\n"
        + `(src/, excluding tests and the generated token module). Remove them from\n`
        + `${BUDGET_FILE}:\n${list(strays)}`,
    ).toEqual([]);
  });

  it('accounts for every file that carries a dialog', () => {
    // Where the teeth are. The default for all 204 scanned files is zero, listed or not,
    // so a dialog in any unlisted one fails here — without anyone having had to predict
    // which file it would appear in. §1.8's table predicted two files; the tree held five.
    const missing = unbudgeted(SCANNED, NATIVE_DIALOG_BUDGET);

    expect(
      missing,
      'A native browser dialog entered a file with no budget entry (Requirement 18.3).\n\n'
        + '  confirm  — use `ds/ConfirmDialog`. It focus-traps, takes an accessible name, and\n'
        + '             can show the review grid a destructive action needs.\n'
        + '  alert    — use `ds/Alert` for an in-page verdict, or `window.showToast` for a\n'
        + '             transient one; `AppShell` installs the transport.\n'
        + '  prompt   — a bare string with nowhere to put a label or a verdict. Use a form.\n\n'
        + 'If the file is a surface design.md §17.2 defers, add an entry to\n'
        + `${BUDGET_FILE} with a note naming the call and the interaction it guards.\n${list(missing)}`,
    ).toEqual([]);
  });

  it('holds every file at or below its entry', () => {
    const over = overBudget(SCANNED, NATIVE_DIALOG_BUDGET);
    expect(
      over,
      'A native dialog was added to a file that already carries one. Raising an out-of-scope\n'
        + `entry in ${BUDGET_FILE} needs a reason in the PR, and an in-scope entry cannot be\n`
        + `raised at all — Requirement 18.3 has no allowance for one more:\n${list(over)}`,
    ).toEqual([]);
  });

  it('requires progress to be recorded, not banked', () => {
    // The second direction. A `<=` assertion would let a contributor remove a dialog and
    // leave the headroom open for the next one to fill.
    const under = underBudget(SCANNED, NATIVE_DIALOG_BUDGET);
    expect(
      under,
      'These files now hold fewer native dialogs than their committed entry. Good — but the\n'
        + 'entry has to come down with them, in this commit, or the headroom stays open. Lower\n'
        + `it to the measured number rather than deleting it, so the file stays held:\n${list(under)}`,
    ).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 5. The in-scope set is closed and empty
// ---------------------------------------------------------------------------

describe('no-native-dialogs: the in-scope set is closed', () => {
  const inScope = Object.keys(IN_SCOPE_NATIVE_DIALOG_BUDGET);
  const outOfScope = Object.keys(OUT_OF_SCOPE_NATIVE_DIALOG_BUDGET);

  it('splits the budget in two, with nothing in both groups and nothing in neither', () => {
    // The flat map is `{...inScope, ...outOfScope}`, so a duplicate key would silently take
    // the out-of-scope number and the emptiness rule below would not reach it.
    const both = inScope.filter((relative) => relative in OUT_OF_SCOPE_NATIVE_DIALOG_BUDGET);
    expect(
      both,
      `These entries are declared in BOTH groups of ${BUDGET_FILE}:\n${list(both)}`,
    ).toEqual([]);

    expect(inScope.length + outOfScope.length).toBe(Object.keys(NATIVE_DIALOG_BUDGET).length);
    expect(new Set([...inScope, ...outOfScope])).toEqual(
      new Set(Object.keys(NATIVE_DIALOG_BUDGET)),
    );
  });

  it('declares the eleven in-scope pages and the two primitives that replaced the calls', () => {
    const missing = [...IN_SCOPE_PAGES, ...REPLACEMENT_PRIMITIVES].filter(
      (relative) => !(relative in IN_SCOPE_NATIVE_DIALOG_BUDGET),
    );
    expect(
      missing,
      'design.md gives these pages a page task in M6-M9, and §1.8/§1.9 name the two\n'
        + 'primitives as the replacements. An in-scope file with no in-scope entry is a file\n'
        + `the emptiness rule below does not reach:\n${list(missing)}`,
    ).toEqual([]);

    const misfiled = IN_SCOPE_PAGES.filter(
      (relative) => relative in OUT_OF_SCOPE_NATIVE_DIALOG_BUDGET,
    );
    expect(
      misfiled,
      `These in-scope pages are filed as out of scope in ${BUDGET_FILE}. Moving a page out\n`
        + `of scope is a change to the spec's M6-M9 list, not to this file:\n${list(misfiled)}`,
    ).toEqual([]);

    // Non-vacuity: if either list were ever emptied, the checks above would pass over
    // nothing. A `ConfirmDialog` that fell back to `window.confirm` for some edge case
    // would defeat the focus trap on every page at once, without any page's entry moving.
    expect(IN_SCOPE_PAGES).toHaveLength(11);
    expect(REPLACEMENT_PRIMITIVES).toHaveLength(2);
    expect(inScope.length).toBeGreaterThanOrEqual(13);
  });

  it('never gives an in-scope entry headroom of any size', () => {
    // Stated over the COMMITTED numbers: the only legal in-scope entry is 0, and a new
    // in-scope page can only be seeded at 0. Requirement 18.3 is explicit that these
    // block the tab, so "temporarily" is not available as an argument.
    const withHeadroom = inScope
      .filter((relative) => IN_SCOPE_NATIVE_DIALOG_BUDGET[relative] !== 0)
      .map((relative) => `${relative} — ${IN_SCOPE_NATIVE_DIALOG_BUDGET[relative]}`);

    expect(
      withHeadroom,
      'An in-scope file may only be entered at 0 — Requirement 18.3 is a statement about\n'
        + `exactly these files:\n${list(withHeadroom)}`,
    ).toEqual([]);
  });

  /**
   * THE ASSERTION THIS TASK EXISTS TO ADD, and it runs live rather than skipped: tasks
   * 10.3 and 10.4 cleared §1.8's six call sites and the two bare `alert(…)` calls the
   * table missed, so the in-scope set is empty because the pages were migrated, not
   * because a list was edited. `declares the eleven in-scope pages`, `names only files
   * that still exist` and `finds the dialogs that are really in the tree` are what keep
   * those two from being confused, and all three run beside this one.
   *
   * Note what each of the pair does. `never gives an in-scope entry headroom` reads the
   * COMMITTED numbers, so it refuses an allowance being written down; this one reads the
   * MEASURED counts, so it refuses a dialog that is really in the tree. Neither subsumes
   * the other.
   */
  it('holds no native dialog on any in-scope page or primitive', () => {
    const dirty = dirtyInScope(SCANNED, IN_SCOPE_NATIVE_DIALOG_BUDGET);

    expect(
      dirty,
      'Requirement 18.3: no in-scope page confirms a destructive action with a native\n'
        + 'browser dialog. `ds/ConfirmDialog` is the replacement. These do:\n'
        + `${list(dirty)}`,
    ).toEqual([]);
  });
});
