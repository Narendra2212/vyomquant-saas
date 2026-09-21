/**
 * `no-placeholders` — vyomquant-ui-redesign task 10.12.
 * Requirement 19.4. design.md §1.9, §7.3, §15.1.
 *
 * ===========================================================================
 * WHAT THIS GUARD IS FOR
 * ===========================================================================
 * Requirement 19.4: "THE VyomQuant_Frontend SHALL NOT contain TODO, FIXME,
 * 'coming soon' placeholder text, non-functional buttons, non-functional links,
 * or non-functional dropdowns on any in-scope page."
 *
 * design.md §1.9 is the audit that found the violations, and it found exactly
 * two: `pages/StrategyDetail.jsx:1179`'s `Audit history coming soon`, and
 * `components/Sidebar.jsx`'s external `docs` target. §7.3 decided the first one
 * — remove the tab, link to Signal Trace, which is the page that owns that data
 * — and task 27.1 carried it out. So the tree is clean as this guard lands, and
 * that is the problem it exists for: nothing stopped the next one. Task 28's
 * exit criteria say "All eight structural guards read clean", and until now one
 * of the eight did not exist, which made that line vacuous for a whole class of
 * defect.
 *
 * The other seven guards measure *presentation* debt — a hex literal, a `C.`
 * reference, a dead Tailwind class. This one measures a different thing: a
 * surface that tells a trader something is there when it is not. There is no
 * migration underway to shrink it, so it is not a ratchet. Its resting state is
 * zero everywhere, and the budget beside it is an allowlist of the handful of
 * out-of-scope files that already carry one.
 *
 * ===========================================================================
 * WHAT COUNTS AS A PLACEHOLDER — THREE RULES, TAKEN FROM 19.4's OWN WORDS
 * ===========================================================================
 *   1. UNIMPLEMENTED-SURFACE COPY. `coming soon` and its neighbours: `under
 *      construction`, `not implemented` / `not yet implemented`, `to be
 *      implemented`, `work in progress`, `stay tuned`, `lorem ipsum`. This is
 *      19.4's "'coming soon' placeholder text" and it is the rule that would
 *      have caught the AuditTab.
 *   2. DEVELOPER MARKERS. `TODO` and `FIXME`, which 19.4 names first.
 *   3. DEAD TARGETS. A handler that is an empty arrow function, and an `href` /
 *      `to` of exactly `"#"`. This is 19.4's "non-functional buttons,
 *      non-functional links", in the two forms a text scan can decide without
 *      guessing.
 *
 * ===========================================================================
 * A MARKER IN A COMMENT VERSUS A MARKER IN RENDERED OUTPUT
 * ===========================================================================
 * The two are not the same defect and the three rules do not treat them the
 * same. `// TODO: wire this up` is a note to a developer. `<p>Coming soon</p>`
 * is a lie to a trader. `no-colour-literals` strips comments before counting
 * because a hex value *named in prose* renders nothing; the same reasoning does
 * not reach the same conclusion for all three rules here, so each one was
 * decided on its own:
 *
 * RULE 1 — COMMENTS STRIPPED. The phrase is only a defect when it reaches the
 * screen, and the codebase documents its removals in prose. This is not
 * hypothetical: `pages/StrategyDetail.jsx:1779-1785` carries the note that
 * `AuditTab` "rendered one centred line of placeholder text and nothing else",
 * and `components/shell/AccountMenu.jsx:53` says "ANNOUNCING a menu one has not
 * implemented is a lie to assistive technology". Both are in-scope files; both
 * are records of a defect being FIXED. A guard that counted them would punish
 * writing down the one part of a migration worth keeping, and the cheapest way
 * to make it pass would be to delete the explanation. So: comments are blanked
 * for rule 1, and the self-tests below pin both of those exact sentences as
 * non-matches.
 *
 * RULE 2 — COMMENTS INCLUDED, i.e. the raw file. `TODO` in a comment is where
 * `TODO` lives; stripping comments first would leave rule 2 policing almost
 * nothing, and 19.4 names the marker rather than the rendering. A `// TODO`
 * beside a control on an in-scope page is an admission that the surface is
 * unfinished, written by the person who would know — the same admission the
 * AuditTab made out loud. The whole of `src/` contains zero `TODO` and zero
 * `FIXME` today, including in comments, so the strict reading costs nothing and
 * records a standard the project already keeps.
 *   The price is real and is accepted deliberately: a future note that wants to
 * *mention* the marker cannot write it literally. That is what the failure
 * message says to do instead — describe it, the way the `AuditTab` note above
 * describes what it replaced without reproducing it.
 *
 * RULE 3 — COMMENTS STRIPPED, and stripping is what makes it work rather than a
 * concession: `onClick={() => {/* nothing yet *\/}}` becomes
 * `onClick={() => {          }}` once the comment is blanked, so the no-op is
 * caught with its excuse removed. `source-scan.js`'s stripper preserves length,
 * which is why that holds.
 *
 * Strings are NOT stripped by any of the three. Rendered copy lives inside
 * strings and JSX text — `<span>Screenshot Coming Soon</span>`,
 * `headline: 'Coming soon'` — and that is exactly what rule 1 exists to find.
 *
 * ===========================================================================
 * THE TRAP THIS GUARD IS MOST PRONE TO
 * ===========================================================================
 * A pattern broad enough to match its own source, or to match ordinary English
 * in a neighbouring file's explanatory prose. `dead-tailwind`'s header records
 * the same class of problem from the other direction — three retired class names
 * appear in `no-colour-literals.test.js`'s docblock, Tailwind v4's content
 * scanner reads the whole project as text, and so a *comment in a test file*
 * materialises CSS in the production bundle.
 *
 * Four narrowings come from that, each with a self-test:
 *
 *   * `soon` ALONE IS NOT A PATTERN. `src/` renders it honestly in at least four
 *     places: "A position appears here as soon as a deployed strategy opens one"
 *     (`pages/Portfolio.jsx`), "this list fills as soon as one is running"
 *     (`pages/SignalTrace.jsx`), "ending soon" (`design/subscriptionState.js`)
 *     and "we'll notify you as soon as your access is ready"
 *     (`components/waitlist/WaitlistForm.jsx`). The pattern is the two-word
 *     phrase, never one word of it.
 *   * `coming` ALONE IS NOT A PATTERN either, and for a sharper reason: the
 *     honest not-available copy is *built* out of it. `lib/signalTraceStages.js`
 *     renders "It is not available rather than pending, which would claim it is
 *     still coming", and `design/pageFields.js` and `design/reported.js` say
 *     versions of the same. Those sentences are Requirement 19.3's remedy. A
 *     guard that flagged them would be pushing the tree toward the defect.
 *   * `placeholder` IS NOT A PATTERN. `placeholder` is an HTML attribute, and
 *     `<input placeholder="Search symbols" />` is a well-formed control, not an
 *     unfinished one. There is no narrowing that separates the attribute from
 *     the accusation, so the word is left out entirely.
 *   * `not available` IS NOT A PATTERN, and must never become one. Requirement
 *     19.3 requires exactly that phrase where a capability is missing, `ds/
 *     Metric`'s `NotAvailableMarker` renders it, and `design/reported.js` exists
 *     to make it specific. It is the cure, not the disease.
 *
 * Two more, from the identifier direction:
 *
 *   * Rule 1's phrases all require a SEPARATOR — whitespace, `-` or `_` —
 *     between the words. So `ScreenshotComingSoon`, the name of the landing
 *     component in the budget, is not a match where it is imported or called.
 *     This guard measures copy, and copy has spaces. (The component's own
 *     rendered chip does not, hence its entry.)
 *   * Rule 2 is case-SENSITIVE. `TODO` and `FIXME` uppercase are the convention
 *     every linter and IDE task list recognises; a lower-case mention inside a
 *     word is not a marker.
 *
 * And the scan roots are all under `src/`, so this file — which necessarily
 * names every pattern it looks for, several times — is outside its own reach,
 * as are the other guards' docblocks. `scans only under src/` asserts that,
 * because it is the assertion that keeps this suite from being self-defeating.
 *
 * ===========================================================================
 * SCOPE
 * ===========================================================================
 * `src/pages/**`, `src/components/**` and `src/lib/**` — the same three roots
 * `no-colour-literals` and `legacy-c-budget` settled on, the third added to both
 * at task 23.3. Task 10.12 names the first two ("the in-scope page and in-scope
 * component files"); `lib/` joins them because a refusal message or an empty-
 * state headline composed in a pure module is rendered copy that no page
 * authored — `lib/signalTraceStages.js` builds four such sentences today.
 *
 * Excluded, and asserted below so a reader sees the decision rather than
 * inferring it from an absence:
 *
 *   * THE TOKEN LAYER — `styles/tokens.css`, `design/tokens.js`, `index.css`.
 *     Named in `TOKEN_LAYER_FILES` and asserted outside the scan. None of the
 *     three is inside a scan root anyway, so this is a statement of intent
 *     rather than a filter, and it fails if a root is ever widened over them.
 *   * TESTS — `__tests__/`, `*.test.*`, `*.spec.*`, via `isTestFile`. A test
 *     asserting a control is inert, or one that names a marker in order to prove
 *     a rule, is doing its job. This file is the obvious case.
 *
 * THE KNOWN GAP, recorded rather than buried, the way `dead-tailwind` records
 * its own ceiling: `src/design/**` is outside the three roots and it does carry
 * rendered copy — `design/pageFields.js` declares tooltips and notes that reach
 * the screen through `ds/Metric`. A "coming soon" written there would be
 * invisible here. It measures zero under all three rules today, so the gap is
 * theoretical at present; closing it means adding a fourth root and excluding
 * `design/tokens.js` from it explicitly, which is a scope decision for the task
 * that first needs it, not one to take silently inside a guard.
 *
 * ===========================================================================
 * WHAT IS DELIBERATELY NOT MEASURED
 * ===========================================================================
 * 19.4 also forbids non-functional DROPDOWNS, and the fuller versions of the
 * dead-control case — a disabled control with no path to enabled, a tab whose
 * whole body is one centred line — are behavioural, not textual. A text scan
 * cannot decide whether a `disabled` prop has a route to `false`. Task 10.13's
 * **Property 37, "No enabled control is inert"** owns that, over task 8.12's
 * page registry, and `ds/CommandButton` already refuses in development to render
 * an inoperable control without a visible reason. Rule 3 takes only the two
 * forms that need no inference. The overlap is deliberate and the division is:
 * this guard decides what the source *says*, Property 37 decides what the
 * rendered tree *does*.
 *
 * `XXX` and `HACK` are not rule 2 patterns. 19.4 names `TODO` and `FIXME`;
 * `XXX` in particular collides with masked identifiers and account numbers, and
 * a guard that cries wolf gets deleted.
 */

import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, it, expect } from 'vitest';

import {
  IN_SCOPE_PLACEHOLDER_BUDGET,
  OUT_OF_SCOPE_PLACEHOLDER_BUDGET,
  PLACEHOLDER_BUDGET,
  TOKEN_LAYER_FILES,
} from './no-placeholders.budget.js';
import { SRC, collect, isTestFile, list, relToSrc, stripComments } from './source-scan.js';

const BUDGET_FILE = 'tests/unit/guards/no-placeholders.budget.js';

/**
 * design.md's M6-M9 page tasks — the same eleven `no-colour-literals` names, and for the
 * same reason: "in-scope page" in Requirement 19.4 means this enumerated set. Written out
 * here so the in-scope group cannot quietly shed a page.
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

/** Task 10.12's two roots, plus the `lib` root task 23.3 gave the sibling guards. */
const SCAN_ROOTS = Object.freeze(['pages', 'components', 'lib']);

/** Copy lives in JSX and in the modules that compose it. `.css` carries none. */
const SCANNED_EXTENSIONS = Object.freeze(['.js', '.jsx', '.ts', '.tsx']);

// ---------------------------------------------------------------------------
// The three patterns
// ---------------------------------------------------------------------------

/**
 * RULE 1. Unimplemented-surface copy. One regex with ordered alternation rather than a
 * list of patterns, so overlapping phrases cannot be counted twice: "not implemented yet"
 * is one finding, not one for `not implemented` and another for `implemented yet`. Longest
 * alternative first, for the same reason `HEX_LITERAL` puts the 8-digit form first.
 *
 * Every phrase requires a `[\s_-]+` separator, so an identifier is never a match — see the
 * TRAP section. `\s` covers the newline, because JSX text wraps.
 */
const UNIMPLEMENTED_COPY =
  /coming[\s_-]+soon|under[\s_-]+construction|not[\s_-]+implemented[\s_-]+yet|not[\s_-]+yet[\s_-]+implemented|not[\s_-]+implemented|to[\s_-]+be[\s_-]+implemented|work[\s_-]+in[\s_-]+progress|stay[\s_-]+tuned|lorem[\s_-]+ipsum/gi;

/** RULE 2. The two markers Requirement 19.4 names. Case-sensitive; see the TRAP section. */
const DEVELOPER_MARKER = /\b(?:TODO|FIXME)\b/g;

/**
 * RULE 3. A control with nothing behind it, in the two forms that need no inference:
 *
 *   `on<Event>={() => {}}`     — including `(e) => {}` and `async () => {}`, and including
 *                                a body that held only a comment, since the comment is
 *                                blanked to spaces before this runs.
 *   `href="#"` / `to="#"`      — exactly `#`. `href="#features"` is a real fragment target
 *                                and is not a match; that near-miss is the one
 *                                `no-colour-literals`' hex pattern was bitten by.
 */
const DEAD_CONTROL =
  /\bon[A-Z][A-Za-z]*\s*=\s*\{\s*(?:async\s+)?\(\s*\w*\s*\)\s*=>\s*\{\s*\}\s*\}|\b(?:href|to)\s*=\s*(?:"#"|'#'|\{\s*(?:"#"|'#'|`#`)\s*\})/g;

// ---------------------------------------------------------------------------
// Counting
// ---------------------------------------------------------------------------

/**
 * Every placeholder in one source file, as the matched text, grouped by rule.
 *
 * Rules 1 and 3 read the comment-stripped source; rule 2 reads the raw source. The
 * asymmetry is the whole design decision of this guard and is argued in the header.
 */
export function findPlaceholders(source) {
  const code = stripComments(source);
  return {
    copy: [...code.matchAll(UNIMPLEMENTED_COPY)].map((m) => m[0]),
    markers: [...source.matchAll(DEVELOPER_MARKER)].map((m) => m[0]),
    deadControls: [...code.matchAll(DEAD_CONTROL)].map((m) => m[0].replace(/\s+/g, ' ')),
  };
}

/** How many placeholders a source file contains, across all three rules. */
export function countPlaceholders(source) {
  const found = findPlaceholders(source);
  return found.copy.length + found.markers.length + found.deadControls.length;
}

/** `{ relative, count, found }` for one file. */
function measure(relative, source) {
  const found = findPlaceholders(source);
  return {
    relative,
    found,
    count: found.copy.length + found.markers.length + found.deadControls.length,
  };
}

/** One report line: the file, the total, and what was actually matched. */
const describeFinding = (f) => {
  const parts = [];
  if (f.found.copy.length) parts.push(`copy: ${f.found.copy.join(', ')}`);
  if (f.found.markers.length) parts.push(`marker: ${f.found.markers.join(', ')}`);
  if (f.found.deadControls.length) parts.push(`dead control: ${f.found.deadControls.join(', ')}`);
  return `${f.relative} — ${f.count} (${parts.join('; ')})`;
};

// ---------------------------------------------------------------------------
// The report builders
// ---------------------------------------------------------------------------
//
// Pure functions of (findings, budget) rather than closures over the real scan, so
// "the guard would fail on a planted example" is a test rather than a claim. Section 3
// hands each of them a synthetic finding and asserts it comes back reported.

/** Files carrying a placeholder that the budget does not name. */
export const unbudgeted = (findings, budget) =>
  findings.filter((f) => f.count > 0 && !(f.relative in budget)).map(describeFinding);

/** Files carrying more than their entry allows. */
export const overBudget = (findings, budget) =>
  findings
    .filter((f) => f.relative in budget && f.count > budget[f.relative])
    .map((f) => `${describeFinding(f)} — budget ${budget[f.relative]}`);

/** Files carrying fewer than their entry allows: progress that was not written down. */
export const underBudget = (findings, budget) =>
  findings
    .filter((f) => f.relative in budget && f.count < budget[f.relative])
    .map((f) => `${f.relative} — lower the committed budget from ${budget[f.relative]} to ${f.count}`);

/** In-scope files carrying a placeholder, whatever their entry says. */
export const dirtyInScope = (findings, inScope) =>
  findings.filter((f) => f.relative in inScope && f.count > 0).map(describeFinding);

// ---------------------------------------------------------------------------
// Scanning
// ---------------------------------------------------------------------------

function scan() {
  const files = [];
  for (const root of SCAN_ROOTS) {
    for (const full of collect(path.join(SRC, root), SCANNED_EXTENSIONS)) {
      const relative = relToSrc(full);
      if (isTestFile(relative)) continue;
      files.push(measure(relative, readFileSync(full, 'utf8')));
    }
  }
  return files.sort((a, b) => a.relative.localeCompare(b.relative));
}

const SCANNED = scan();

// ---------------------------------------------------------------------------
// 1. The counting method itself
// ---------------------------------------------------------------------------

describe('no-placeholders: rule 1, unimplemented-surface copy', () => {
  it('finds the phrase however it is spaced, cased or wrapped', () => {
    expect(countPlaceholders('<p>Coming soon</p>')).toBe(1);
    expect(countPlaceholders('<span>COMING SOON</span>')).toBe(1);
    expect(countPlaceholders("headline: 'Audit history coming soon'")).toBe(1);
    // JSX text wraps, so the separator has to admit a newline.
    expect(countPlaceholders('<p>\n  Coming\n  soon\n</p>')).toBe(1);
    expect(countPlaceholders('data-state="coming-soon"')).toBe(1);
    expect(countPlaceholders('<p>Under construction</p><p>Stay tuned</p>')).toBe(2);
    expect(countPlaceholders('<p>Lorem ipsum dolor</p>')).toBe(1);
    expect(countPlaceholders('<p>This tab is work in progress</p>')).toBe(1);
  });

  it('counts an overlapping phrase once, not twice', () => {
    // Ordered alternation, longest first: one finding each, not two.
    expect(countPlaceholders('<p>Not implemented yet</p>')).toBe(1);
    expect(countPlaceholders('<p>Not yet implemented</p>')).toBe(1);
    expect(countPlaceholders('<p>To be implemented</p>')).toBe(1);
  });

  it('does not fire on "soon" in honest copy', () => {
    // Four real sentences from src/. See the TRAP section: `soon` alone is not a pattern.
    expect(
      countPlaceholders('"A position appears here as soon as a deployed strategy opens one."'),
    ).toBe(0);
    expect(countPlaceholders("'this list fills as soon as one is running'")).toBe(0);
    expect(countPlaceholders('"ending soon" without lying about current access')).toBe(0);
    expect(countPlaceholders("`We'll notify you as soon as your access is ready.`")).toBe(0);
  });

  it('does not fire on the not-available copy Requirement 19.3 requires', () => {
    // `lib/signalTraceStages.js` renders this. It is the cure, not the disease — and note
    // that it contains the word `coming`, one token away from a false positive.
    expect(
      countPlaceholders(
        "'not available rather than pending, which would claim it is still coming.'",
      ),
    ).toBe(0);
    expect(countPlaceholders("'Nothing is coming; the record existed and was swept.'")).toBe(0);
    expect(countPlaceholders('<NotAvailableMarker reason="the connector does not report it" />')).toBe(
      0,
    );
  });

  it('does not fire on the word "placeholder", which is an HTML attribute', () => {
    expect(countPlaceholders('<input placeholder="Search symbols" />')).toBe(0);
    expect(countPlaceholders("<Field placeholder={'e.g. 10000'} />")).toBe(0);
  });

  it('does not fire on an identifier, because copy has separators', () => {
    expect(countPlaceholders("import ScreenshotComingSoon from './ScreenshotComingSoon'")).toBe(0);
    expect(countPlaceholders('<ScreenshotComingSoon title="Dashboard" />')).toBe(0);
  });

  it('ignores the phrase in a comment, which is how a removal is documented', () => {
    // Both of these are real in-scope prose. `pages/StrategyDetail.jsx:1780` records the
    // AuditTab removal; `components/shell/AccountMenu.jsx:53` explains a refusal. A guard
    // that counted either would make deleting the explanation the cheapest way to pass.
    expect(
      countPlaceholders(
        [
          '/*',
          ' * `AuditTab` was here (§1.9, Requirement 19.4). It rendered one centred line of',
          ' * placeholder text and nothing else — a tab promising a record this page never fetched.',
          ' */',
        ].join('\n'),
      ),
    ).toBe(0);
    expect(
      countPlaceholders(
        '// ANNOUNCING a menu one has not implemented is a lie to assistive technology',
      ),
    ).toBe(0);
    expect(countPlaceholders('/* the `Audit history coming soon` panel, removed at 27.1 */')).toBe(0);
  });

  it('still counts copy on a line that also carries a comment', () => {
    expect(countPlaceholders('<p>Coming soon</p> // remove at M9')).toBe(1);
  });
});

describe('no-placeholders: rule 2, developer markers', () => {
  it('finds the markers Requirement 19.4 names, including in comments', () => {
    // The deliberate asymmetry with rule 1. A marker in a comment is where a marker lives.
    expect(countPlaceholders('// TODO: wire this to the risk endpoint')).toBe(1);
    expect(countPlaceholders('/* FIXME the close button does nothing */')).toBe(1);
    expect(countPlaceholders(' * @todo\n * TODO\n')).toBe(1);
    expect(countPlaceholders('<span>TODO</span>')).toBe(1);
    expect(countPlaceholders('const label = "FIXME";')).toBe(1);
  });

  it('is case-sensitive and respects word boundaries', () => {
    expect(countPlaceholders('const todos = [];')).toBe(0);
    expect(countPlaceholders('function fixMeUp() {}')).toBe(0);
    expect(countPlaceholders('const TODOS_REMAINING = 0;')).toBe(0);
  });

  it('leaves XXX and HACK alone, which 19.4 does not name', () => {
    expect(countPlaceholders('const masked = "XXXX-1234";')).toBe(0);
    expect(countPlaceholders('// HACK: rounding')).toBe(0);
  });
});

describe('no-placeholders: rule 3, dead targets', () => {
  it('finds an empty handler and a dead link', () => {
    expect(countPlaceholders('<button onClick={() => {}}>Close</button>')).toBe(1);
    expect(countPlaceholders('<button onClick={() => { }}>Close</button>')).toBe(1);
    expect(countPlaceholders('<select onChange={(e) => {}} />')).toBe(1);
    expect(countPlaceholders('<form onSubmit={async () => {}} />')).toBe(1);
    expect(countPlaceholders('<a href="#">Docs</a>')).toBe(1);
    expect(countPlaceholders("<Link to='#'>Docs</Link>")).toBe(1);
    expect(countPlaceholders('<Link to={"#"}>Docs</Link>')).toBe(1);
  });

  it('sees through a comment used as the body, because the comment is blanked first', () => {
    // Stripping is load-bearing here rather than a concession — see the header.
    expect(countPlaceholders('<button onClick={() => {/* nothing yet */}} />')).toBe(1);
  });

  it('does not fire on a handler that does something', () => {
    expect(countPlaceholders('<button onClick={() => setOpen(false)} />')).toBe(0);
    expect(countPlaceholders('<button onClick={handleClose} />')).toBe(0);
    expect(countPlaceholders('<button onClick={() => { close(); }} />')).toBe(0);
    expect(countPlaceholders('<Panel onRetry={() => refetch({ force: true })} />')).toBe(0);
  });

  it('does not mistake a real fragment target for a dead one', () => {
    // The near-miss `no-colour-literals`' hex pattern was bitten by, from the other side.
    expect(countPlaceholders('<a href="#features">Features</a>')).toBe(0);
    expect(countPlaceholders('<a href="#main-content">Skip to content</a>')).toBe(0);
    expect(countPlaceholders('<Link to="/app/strategies">Strategies</Link>')).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// 2. Scope
// ---------------------------------------------------------------------------

describe('no-placeholders: scope', () => {
  it('scans src/pages, src/components and src/lib, and nothing else', () => {
    for (const { relative } of SCANNED) {
      expect(
        SCAN_ROOTS.some((root) => relative.startsWith(`${root}/`)),
        `${relative} is outside ${SCAN_ROOTS.map((root) => `src/${root}`).join(', ')}`,
      ).toBe(true);
    }
  });

  it('scans only under src/, so this file is outside its own reach', () => {
    // The trap in this guard's header, asserted rather than assumed. Every pattern above
    // appears in this file several times, and so do several of them in the other guards'
    // prose. If a root were ever widened to the repo, or to `tests/`, the suite would start
    // failing on its own explanation — and the cheapest fix would be to delete the
    // explanation. `SRC` is `<repo>/algo22-terminal/src`; the roots are joined onto it.
    for (const root of SCAN_ROOTS) {
      const resolved = path.join(SRC, root);
      expect(resolved.startsWith(SRC + path.sep), `${root} escapes src/`).toBe(true);
      expect(existsSync(resolved), `src/${root} is missing`).toBe(true);
    }
    expect(SCANNED.some((f) => f.relative.includes('tests/'))).toBe(false);
  });

  it('leaves the token layer out, on purpose and on the record', () => {
    // None of the three is inside a scan root, so this is a statement of intent that
    // becomes a filter the moment a root is widened over one of them.
    for (const relative of TOKEN_LAYER_FILES) {
      expect(existsSync(path.join(SRC, relative)), `${relative} is missing`).toBe(true);
      expect(SCANNED.some((f) => f.relative === relative)).toBe(false);
      expect(Object.keys(PLACEHOLDER_BUDGET)).not.toContain(relative);
    }
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
// 3. Non-vacuity — the guard is live
// ---------------------------------------------------------------------------

describe('no-placeholders: the guard is live, not vacuous', () => {
  it('reads a non-trivial number of files, in every root', () => {
    // A guard whose scan reached nothing would pass every assertion below in silence —
    // `source-scan.js`'s header calls that the worst outcome a guard can have, and it is the
    // failure mode the `import.meta.url` note there exists for.
    //
    // THE FILE-COUNT FLOOR IS A LIVENESS TRIPWIRE, NOT A BUDGET, and is set well below the
    // 136 files the three roots hold today. Files legitimately leave the tree —
    // `components/ui-legacy/primitives.jsx` goes at task 27.2 — so re-measuring this to the
    // current total would only trip it on the next deletion. 100 is far enough below to
    // absorb that and far above the handful-of-files case it exists to catch.
    expect(SCANNED.length).toBeGreaterThan(100);
    for (const root of SCAN_ROOTS) {
      expect(
        SCANNED.some((f) => f.relative.startsWith(`${root}/`)),
        `src/${root} is not being scanned`,
      ).toBe(true);
    }
    // Three named files, one per root, so a `collect` that stopped descending is caught.
    for (const relative of [
      'pages/Dashboard.jsx',
      'components/ds/Panel.jsx',
      'lib/signalTraceStages.js',
    ]) {
      expect(
        SCANNED.some((f) => f.relative === relative),
        `${relative} was not reached by the scan`,
      ).toBe(true);
    }
  });

  it('finds the placeholder that is really in the tree', () => {
    // THE FIXED POINT, and this guard's equivalent of `legacy-c-budget`'s "actually finds
    // the shim". `components/landing/ScreenshotComingSoon.jsx` renders one chip reading
    // `Screenshot Coming Soon`. If path resolution broke, or rule 1 stopped matching, every
    // count would be 0 and only the under-budget direction would object — so a real file
    // that MUST measure non-zero is what proves the scan ran.
    const fixture = 'components/landing/ScreenshotComingSoon.jsx';
    const found = SCANNED.find((f) => f.relative === fixture);

    expect(existsSync(path.join(SRC, fixture)), `${fixture} is missing`).toBe(true);
    expect(found, `${fixture} was not reached by the scan`).toBeDefined();
    expect(found.found.copy).toEqual(['Coming Soon']);
    expect(OUT_OF_SCOPE_PLACEHOLDER_BUDGET[fixture]).toBeGreaterThan(0);
  });

  it('would report a planted placeholder on an in-scope page', () => {
    // The assertions in sections 4 and 5 all compare a list against `[]`. A list that is
    // always empty passes whatever the tree holds, so the builders are exercised here on a
    // planted finding instead of only on the real scan.
    const planted = measure(
      'pages/Dashboard.jsx',
      [
        '// TODO: wire the halt button',
        'export const AuditTab = () => <p>Audit history coming soon</p>;',
        'export const Dead = () => <button onClick={() => {}}>Halt</button>;',
      ].join('\n'),
    );

    expect(planted.count).toBe(3);
    expect(planted.found).toEqual({
      copy: ['coming soon'],
      markers: ['TODO'],
      deadControls: ['onClick={() => {}}'],
    });

    // Every gate that guards an in-scope page reports it.
    expect(dirtyInScope([planted], IN_SCOPE_PLACEHOLDER_BUDGET)).toHaveLength(1);
    expect(overBudget([planted], PLACEHOLDER_BUDGET)).toHaveLength(1);
    expect(unbudgeted([planted], {})).toHaveLength(1);
    // And the report names the file and what was matched, not just a count.
    expect(dirtyInScope([planted], IN_SCOPE_PLACEHOLDER_BUDGET)[0]).toContain('pages/Dashboard.jsx');
    expect(dirtyInScope([planted], IN_SCOPE_PLACEHOLDER_BUDGET)[0]).toContain('coming soon');
  });

  it('would report a placeholder in a file no budget names', () => {
    const planted = measure('components/ds/Panel.jsx', '<a href="#">Docs</a>');

    expect(planted.count).toBe(1);
    expect(unbudgeted([planted], PLACEHOLDER_BUDGET)).toHaveLength(1);
  });

  it('would report a cleaned file whose entry was left behind', () => {
    const cleaned = measure('components/landing/ScreenshotComingSoon.jsx', '<p>A screenshot</p>');

    expect(cleaned.count).toBe(0);
    expect(underBudget([cleaned], PLACEHOLDER_BUDGET)).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// 4. The budget is an allowlist, asserted exactly
// ---------------------------------------------------------------------------

describe('no-placeholders: the budget', () => {
  it('is well formed and frozen', () => {
    for (const map of [IN_SCOPE_PLACEHOLDER_BUDGET, OUT_OF_SCOPE_PLACEHOLDER_BUDGET]) {
      expect(Object.isFrozen(map)).toBe(true);
    }
    for (const [relative, budget] of Object.entries(PLACEHOLDER_BUDGET)) {
      expect(Number.isInteger(budget), `${relative}: ${budget} is not an integer`).toBe(true);
      expect(budget, `${relative}: ${budget} is negative`).toBeGreaterThanOrEqual(0);
      expect(relative, `${relative} must be relative to src/ with forward slashes`).toMatch(
        /^[\w.-]+(?:\/[\w.-]+)*$/,
      );
    }
  });

  it('names only files that still exist', () => {
    const gone = Object.keys(PLACEHOLDER_BUDGET).filter(
      (relative) => !existsSync(path.join(SRC, relative)),
    );
    expect(
      gone,
      `These budget entries name files that are no longer in src/. A deleted file takes its\n`
        + `entry with it — remove them from ${BUDGET_FILE}:\n${list(gone)}`,
    ).toEqual([]);
  });

  it('has an entry for every file it claims to track, and no strays', () => {
    const scannedNames = new Set(SCANNED.map((f) => f.relative));
    const strays = Object.keys(PLACEHOLDER_BUDGET).filter(
      (relative) => !scannedNames.has(relative),
    );
    expect(
      strays,
      `These budget entries name files that exist but are outside this guard's scan\n`
        + `(src/pages, src/components and src/lib, excluding tests). Remove them from\n`
        + `${BUDGET_FILE} or widen SCAN_ROOTS deliberately:\n${list(strays)}`,
    ).toEqual([]);
  });

  it('accounts for every file that carries a placeholder', () => {
    // Unlike `no-colour-literals`, this budget is an ALLOWLIST and not a per-file ledger:
    // the default for every one of the scanned files is zero, listed or not. That is where
    // this guard's teeth are — a placeholder in any of the 100-plus files with no entry
    // fails here, without anyone having had to predict which file it would appear in.
    const missing = unbudgeted(SCANNED, PLACEHOLDER_BUDGET);

    expect(
      missing,
      `A placeholder entered a file with no budget entry (Requirement 19.4).\n\n`
        + `  copy          — render the honest state instead: \`ds/Metric\`'s not-available\n`
        + `                  marker with a reason from \`design/reported.js\`, or \`ds/EmptyState\`.\n`
        + `  marker        — a TODO or FIXME on a rendered surface is an unfinished surface.\n`
        + `                  Finish it, or remove the surface the way §7.3 removed the audit tab.\n`
        + `  dead control  — give it a handler or a target, or disable it with a visible reason\n`
        + `                  (\`ds/CommandButton\` requires one).\n\n`
        + `If the file is genuinely out of scope for the redesign, add an entry to\n`
        + `${BUDGET_FILE} with a note naming why.\n${list(missing)}`,
    ).toEqual([]);
  });

  it('holds every file at or below its entry', () => {
    const over = overBudget(SCANNED, PLACEHOLDER_BUDGET);
    expect(
      over,
      `Placeholders were added to a file that already carries some. Raising an entry in\n`
        + `${BUDGET_FILE} needs a reason in the PR; Requirement 19.4 has no allowance for\n`
        + `one more:\n${list(over)}`,
    ).toEqual([]);
  });

  it('requires progress to be recorded, not banked', () => {
    // The second direction, for the same reason `no-colour-literals` asserts it: headroom
    // left open after a clean-up is headroom a placeholder can creep back into. Here the
    // fix for an emptied entry is to DELETE it — see the out-of-scope rule below.
    const under = underBudget(SCANNED, PLACEHOLDER_BUDGET);
    expect(
      under,
      `These files now hold fewer placeholders than their committed entry. Good — but the\n`
        + `entry has to come down with them, in this commit, or the headroom stays open. An\n`
        + `entry that reaches 0 is deleted rather than kept: the default for an unlisted file\n`
        + `is already zero. Edit ${BUDGET_FILE}:\n${list(under)}`,
    ).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 5. The in-scope set is closed and empty
// ---------------------------------------------------------------------------

describe('no-placeholders: the in-scope set is closed', () => {
  const inScope = Object.keys(IN_SCOPE_PLACEHOLDER_BUDGET);
  const outOfScope = Object.keys(OUT_OF_SCOPE_PLACEHOLDER_BUDGET);

  it('splits the budget in two, with nothing in both groups and nothing in neither', () => {
    // The flat map is `{...inScope, ...outOfScope}`, so a duplicate key would silently take
    // the out-of-scope number and the emptiness rule below would not reach it.
    const both = inScope.filter((relative) => relative in OUT_OF_SCOPE_PLACEHOLDER_BUDGET);
    expect(
      both,
      `These entries are declared in BOTH groups of ${BUDGET_FILE}:\n${list(both)}`,
    ).toEqual([]);

    expect(inScope.length + outOfScope.length).toBe(Object.keys(PLACEHOLDER_BUDGET).length);
    expect(new Set([...inScope, ...outOfScope])).toEqual(new Set(Object.keys(PLACEHOLDER_BUDGET)));
  });

  it('declares the eleven in-scope pages, and no page from that list out of scope', () => {
    const missing = IN_SCOPE_PAGES.filter(
      (relative) => !(relative in IN_SCOPE_PLACEHOLDER_BUDGET),
    );
    expect(
      missing,
      `design.md gives these pages a page task in M6-M9, so Requirement 19.4's "in-scope\n`
        + `page" means them. An in-scope page with no in-scope entry is a page the emptiness\n`
        + `rule below does not reach:\n${list(missing)}`,
    ).toEqual([]);

    const misfiled = IN_SCOPE_PAGES.filter(
      (relative) => relative in OUT_OF_SCOPE_PLACEHOLDER_BUDGET,
    );
    expect(
      misfiled,
      `These in-scope pages are filed as out of scope in ${BUDGET_FILE}. Moving a page out\n`
        + `of scope is a change to the spec's M6-M9 list, not to this file:\n${list(misfiled)}`,
    ).toEqual([]);

    // Non-vacuity: if IN_SCOPE_PAGES were ever emptied, the two checks above would pass
    // over nothing.
    expect(IN_SCOPE_PAGES).toHaveLength(11);
    expect(inScope.length).toBeGreaterThanOrEqual(11);
  });

  it('never gives an in-scope entry headroom of any size', () => {
    // Stated over the COMMITTED numbers. An in-scope file may not be *given* an allowance:
    // the only legal in-scope entry is 0, and unlike a colour literal there is no migration
    // that would justify a temporary one. The only way to legitimise a placeholder on one of
    // these pages is to change the spec's own M6-M9 list.
    const withHeadroom = inScope
      .filter((relative) => IN_SCOPE_PLACEHOLDER_BUDGET[relative] !== 0)
      .map((relative) => `${relative} — ${IN_SCOPE_PLACEHOLDER_BUDGET[relative]}`);

    expect(
      withHeadroom,
      `An in-scope file may only be entered at 0 — Requirement 19.4 is a statement about\n`
        + `exactly these files. ${list(withHeadroom)}`,
    ).toEqual([]);
  });

  it('keeps no emptied entry in the out-of-scope group', () => {
    // The default for an unlisted file is zero, so a `0` out of scope holds nothing — it is
    // an allowance nobody needs, and it would let a placeholder return to a file that had
    // been cleaned. The in-scope group is the opposite case: there a `0` is the rule itself.
    const empty = outOfScope.filter((relative) => OUT_OF_SCOPE_PLACEHOLDER_BUDGET[relative] === 0);
    expect(
      empty,
      `These out-of-scope entries allow 0 placeholders, which is what an absent entry\n`
        + `already means. Delete them from ${BUDGET_FILE}:\n${list(empty)}`,
    ).toEqual([]);
  });

  /**
   * THE ASSERTION THIS TASK EXISTS TO ADD.
   *
   * It runs, un-skipped, because design.md §1.9's two findings are both resolved: task 27.1
   * removed the `Audit history coming soon` tab per §7.3, and task 8.6 rebuilt `Sidebar.jsx`
   * against `shell/navigation.js`. The in-scope set is empty because the pages were
   * migrated, not because a list was edited — `declares the eleven in-scope pages` and
   * `names only files that still exist` are what keep those two facts from being confused,
   * and both run beside this one.
   *
   * Note what each of the pair does. `never gives an in-scope entry headroom` reads the
   * COMMITTED numbers, so it refuses an allowance being written down; this one reads the
   * MEASURED counts, so it refuses a placeholder that is really in the tree. Neither
   * subsumes the other.
   */
  it('holds no placeholder on any in-scope page', () => {
    const dirty = dirtyInScope(SCANNED, IN_SCOPE_PLACEHOLDER_BUDGET);

    expect(
      dirty,
      `Requirement 19.4: no in-scope page carries a TODO, a FIXME, "coming soon" copy, or a\n`
        + `control with nothing behind it. These do:\n${list(dirty)}`,
    ).toEqual([]);
  });
});
