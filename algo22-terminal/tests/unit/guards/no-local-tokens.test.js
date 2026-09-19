/**
 * `no-local-tokens` — vyomquant-ui-redesign task 1.11. Requirement 1.1.
 * design.md §3.4, §15.1.
 *
 * ===========================================================================
 * WHAT THIS GUARD IS FOR
 * ===========================================================================
 * design.md §15.1: "No `const C =` / `const COLORS =` / `const THEME =` outside
 * the shim."
 *
 * Requirement 1.1 puts every colour in `src/styles/tokens.css`. The way that
 * requirement actually got broken in this codebase was not by a stray hex digit
 * — task 1.10's guard covers those — it was by a *second declaration site*.
 * `SupportCenter.jsx` and `NotificationCenter.jsx` each declared their own
 * `const C = { … }` with their own background (`#010608` and `#0a0a0a`), so the
 * app had three canvas colours and no way to tell which was intended
 * (design.md §1.1 G2, §17.2). Task 1.6 replaced both with an import of the shim.
 *
 * This guard is what stops the fourth one appearing. It is a *structural* rule,
 * matched on the declaration name rather than on the value, because that is what
 * makes it unarguable: a reviewer does not have to judge whether a new local
 * object is "really" a palette.
 *
 * ===========================================================================
 * THERE IS NO SANCTIONED DECLARATION ANY MORE — CLEARED AT TASK 27.2
 * ===========================================================================
 * This section used to describe the one exemption the rule carried.
 * `src/components/ui-legacy/primitives.jsx` exported `const C`: the derived
 * compatibility shim from task 1.3, every one of its values a `token.*`
 * reference, so it was a *projection* of the single source rather than a
 * competing one. It existed so the ~700 `C.*` call sites kept resolving while
 * the pages were migrated one at a time, and the plan recorded here was that it
 * would be deleted at task 27.2.
 *
 * TASK 27.2's FINAL STAGE B DID THAT. The shim is deleted. Its eleven components
 * that still had callers were rehomed to `components/common/primitives.jsx` in
 * stage A — reading `token` from `design/tokens.js` directly, declaring no `C` —
 * and the other 25 exports went with the file, reachable only through a
 * `components/ui/index.js` re-export that nothing imported. `legacyTokenShim.js`'s
 * test, which asserted the shim was frozen, derived and closed, is deleted too;
 * so is task 1.12's `legacy-c-budget.test.js`, which had counted the call sites
 * down to zero and had nothing left to count.
 *
 * So the whole of `src/` now declares NO local token object, and this guard
 * asserts exactly that. §15.1 reads "No `const C =` / `const COLORS =` /
 * `const THEME =` outside the shim"; with no shim, the qualifier has nothing to
 * except and the rule is absolute. There is one token source, `tokens.css`, and
 * one JavaScript projection of it, `design/tokens.js` — and `token` is not a name
 * on §15.1's list, because a `token.*` read is a reference to the single source
 * rather than a second declaration of it.
 *
 * What this costs is the guard's old fixed point. The shim's own `export const C =`
 * was what proved the scan had reached real files and the pattern still fired;
 * an all-clear guard has no such anchor, so a broken path resolution would leave
 * `FOUND` empty and every assertion below would pass over nothing. Section 2
 * replaces it: the file list is checked for size and shape, and the pipeline is
 * re-run over the bytes of a real source file with a declaration appended, which
 * exercises read, comment-strip and match together on real content.
 *
 * ===========================================================================
 * FINDING: RESOLVED AT TASK 16.2 — `pages/Portfolio.jsx`
 * ===========================================================================
 * The account below is kept because it records WHY the quarantine existed and
 * what closed it, which a deleted comment would not. As of task 16.2 the guard
 * PASSES CLEAN with no exception at all: the M7 Portfolio migration removed the
 * `const COLORS` array exactly as this note predicted, so `SCHEDULED_EXCEPTIONS`
 * is `{}` and `legacy-c` for that file is 0. Everything from here to the end of
 * this docblock is the historical record.
 *
 * Task 1.11 was written on the expectation that tasks 1.3 and 1.6 had cleared
 * every page-local declaration, leaving the shim as the only one. They cleared
 * the two `const C =` objects. They did not clear this, at `Portfolio.jsx:284`:
 *
 *     const COLORS = [C.orange, C.purple, C.cyan, C.gold, C.t3];
 *
 * It is the allocation pie chart's series palette. So the rule as §15.1 states
 * it has exactly one violation, and it is a real one by the letter of the rule.
 *
 * In substance it is a weaker violation than the two that motivated the rule: it
 * declares no colour. Every element is a reference to the shim, which is itself
 * derived from `tokens.css`, so there is still only one declaration site for
 * these values. It is a *series ordering* — "which token does allocation slice
 * three get" — wearing a name the rule reserves.
 *
 * Rather than weaken the pattern to let it through (which would also let a
 * genuine `const COLORS = { profit: '#00C853' }` through), it is quarantined:
 * named below in `SCHEDULED_EXCEPTIONS`, asserted to be *exactly* that one entry
 * so the list cannot grow quietly, and asserted to contain no colour literal so
 * the quarantine cannot become a hiding place for a real palette. A new
 * violation anywhere in `src/` still fails.
 *
 * It clears when Portfolio is migrated (M7): the chart moves onto the design
 * system's series palette, and the same edit takes `pages/Portfolio.jsx` from 5
 * to 0 in task 1.12's `legacy-c` budget. Both guards clear together. When it
 * does, delete the entry — the test fails if the exception is stale, so this
 * cannot be forgotten.
 *
 * WHAT ACTUALLY HAPPENED (task 16.2). The chart did move onto the series palette,
 * and the array was not re-pointed onto five design-system tokens — it was
 * deleted. `ds/Chart` has no `pie` kind and exposes no colour prop, and
 * `styles/tokens.css` has no fifth CATEGORICAL hue to give a five-slice pie
 * (every non-brand hue already means live, profit, loss, warning or paper). The
 * allocation is a bar chart with one `brand` series, so assets are distinguished
 * by position on a labelled axis and no series ordering exists to declare.
 *
 * Two related notes recorded while measuring, not fixed here (task 1.11 adds no
 * source edits):
 *
 *   * After task 1.3 collapsed the palette, `C.orange`, `C.gold` and
 *     `C.warning` are the same amber, so this five-colour series now renders
 *     four distinct colours — slices 1 and 4 are identical. Worth catching in
 *     the M7 chart work.
 *   * `C.t3` is `--color-content-muted` (#5A6578), annotated NON-TEXT ONLY at
 *     3.2:1. As a chart fill it is used legitimately; its adjacent label is a
 *     separate question for M7.
 *
 * ===========================================================================
 * METHOD
 * ===========================================================================
 * Comments are stripped before matching (see `source-scan.js`), so a docblock
 * that *writes down* the forbidden pattern — this one does, several times — is
 * not itself a violation. Strings are left intact, and the self-tests below pin
 * the boundary cases: `const c =` and `const colors =` are ordinary local
 * variables and must not trip the guard, and `const CONSTANTS =` /
 * `const COLORS_BY_STATUS =` are different identifiers.
 */

import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, it, expect } from 'vitest';

import { SRC, collect, isTestFile, list, relToSrc, stripComments } from './source-scan.js';

/**
 * The deleted shim. Kept as a constant only so the test below can assert it is
 * GONE — `const SHIM` used to name the one declaration the rule permitted.
 */
const DELETED_SHIM = 'components/ui-legacy/primitives.jsx';

/**
 * Declarations that exist today, are scheduled for removal, and are known not to
 * be competing token sources. See the FINDING section above.
 *
 * Adding an entry needs a reason in the PR description and a named task that
 * removes it. Removing an entry is routine — it is part of the migration task
 * that clears the declaration.
 */
const SCHEDULED_EXCEPTIONS = Object.freeze({
  // `pages/Portfolio.jsx: 'COLORS'` was here — the allocation pie's five-element series
  // ordering, every element a shim reference. Task 16.2 deleted it exactly as the FINDING
  // above predicted: the allocation renders through `ds/Chart`, whose series palette comes
  // from `design/semantic.js` and which exposes no colour prop, so there is no array to
  // order. `legacy-c` went 5 -> 0 in the same change, and this guard now passes clean with
  // no exception at all.
});

/** `C`, `COLORS`, `THEME` — case-sensitive, exactly as §15.1 names them. */
const FORBIDDEN = Object.freeze(['C', 'COLORS', 'THEME']);

/**
 * `const <NAME> =`, optionally `export`ed.
 *
 * `\b` before `const` so `myconst` is not a declaration. `\s*=` immediately
 * after the name, and `(?![\w$])` after it, so `COLORS_BY_STATUS` and
 * `CONSTANTS` are different identifiers rather than prefixes of a match.
 * `let`/`var` are not matched: §15.1 says `const`, and every declaration this
 * rule is about is a module- or component-level constant.
 */
const declarationPattern = (name) =>
  new RegExp(String.raw`\b(?:export\s+)?const\s+${name}(?![\w$])\s*=`, 'g');

/** `C` is a JavaScript identifier, so only JavaScript files can declare one. */
const SCANNED_EXTENSIONS = Object.freeze(['.js', '.jsx', '.ts', '.tsx']);

// ---------------------------------------------------------------------------
// Scanning
// ---------------------------------------------------------------------------

/** Which forbidden names a source file declares, with line numbers. */
export function findLocalTokenDeclarations(source) {
  const code = stripComments(source);
  const hits = [];
  for (const name of FORBIDDEN) {
    for (const match of code.matchAll(declarationPattern(name))) {
      hits.push({ name, line: code.slice(0, match.index).split('\n').length });
    }
  }
  return hits.sort((a, b) => a.line - b.line);
}

/** Every declaration of a forbidden name anywhere in `src/`. */
function scan() {
  const found = [];
  for (const full of collect(SRC, SCANNED_EXTENSIONS)) {
    const relative = relToSrc(full);
    if (isTestFile(relative)) continue;
    for (const hit of findLocalTokenDeclarations(readFileSync(full, 'utf8'))) {
      found.push({ relative, ...hit });
    }
  }
  return found;
}

const FOUND = scan();

const describeHit = (h) => `${h.relative}:${h.line} — const ${h.name} =`;

// ---------------------------------------------------------------------------
// 1. The matching method itself
// ---------------------------------------------------------------------------

describe('no-local-tokens: the matching method', () => {
  const names = (source) => findLocalTokenDeclarations(source).map((h) => h.name);

  it('finds each of the three forbidden declarations', () => {
    expect(names("const C = { bg: '#010608' };")).toEqual(['C']);
    expect(names('const COLORS = [1, 2];')).toEqual(['COLORS']);
    expect(names('const THEME = {};')).toEqual(['THEME']);
    expect(names('export const C = Object.freeze({});')).toEqual(['C']);
  });

  it('is case-sensitive, so ordinary local variables are not violations', () => {
    // `deployPreflight.js` has `const c = normalizeCondition(condition)`, and
    // `common/primitives.jsx` has `const colors = {…}` status maps in `Toast` and
    // `StatusDot` — every value a `token.*` read. None of them is a token source
    // and none may fail this guard.
    expect(names('const c = normalizeCondition(condition);')).toEqual([]);
    expect(names('const colors = { BUY: "green", SELL: "red" };')).toEqual([]);
    expect(names('const theme = useTheme();')).toEqual([]);
  });

  it('does not match a longer identifier that merely starts with one', () => {
    expect(names('const CONSTANTS = {};')).toEqual([]);
    expect(names('const COLORS_BY_STATUS = {};')).toEqual([]);
    expect(names('const THEMES = [];')).toEqual([]);
    expect(names('const C2 = {};')).toEqual([]);
    expect(names('const C_MAP = {};')).toEqual([]);
    expect(names('const myconst = 1; const CX = 2;')).toEqual([]);
  });

  it('ignores the pattern written inside a comment', () => {
    // Without this, the docblock at the top of this file and the one in
    // primitives.jsx would each register as violations.
    expect(names('// const C = { … } is forbidden outside the shim')).toEqual([]);
    expect(names('/* replaced `const COLORS =` with the shim */')).toEqual([]);
    expect(names(['/**', ' * const THEME = {} — see §15.1', ' */'].join('\n'))).toEqual([]);
  });

  it('still finds a declaration on a line that also carries a comment', () => {
    expect(names('const C = {}; // local palette, should fail')).toEqual(['C']);
  });

  it('tolerates whitespace the way a formatter would leave it', () => {
    expect(names('const   COLORS   =   [];')).toEqual(['COLORS']);
    expect(names('const C\n  = {};')).toEqual(['C']);
  });

  it('reports the line the declaration is on', () => {
    const hits = findLocalTokenDeclarations('const a = 1;\nconst b = 2;\nconst THEME = {};\n');
    expect(hits).toEqual([{ name: 'THEME', line: 3 }]);
  });
});

// ---------------------------------------------------------------------------
// 2. Scope — and non-vacuity
// ---------------------------------------------------------------------------

describe('no-local-tokens: scope', () => {
  it('scans all of src/, not just the in-scope pages', () => {
    // Task 1.11 says "anywhere in `src/`". A local palette in `lib/` or in a
    // deferred page is exactly as much a second declaration site as one in
    // Dashboard.jsx.
    const scanned = collect(SRC, SCANNED_EXTENSIONS)
      .map(relToSrc)
      .filter((r) => !isTestFile(r));

    expect(scanned.length).toBeGreaterThan(80);
    for (const dir of ['pages', 'components', 'lib', 'api']) {
      expect(
        scanned.some((r) => r.startsWith(`${dir}/`)),
        `src/${dir} is not being scanned`,
      ).toBe(true);
    }
    expect(scanned).toContain('App.jsx');
    expect(scanned).toContain('components/common/primitives.jsx');
  });

  it('leaves test files out', () => {
    expect(isTestFile('api/modules/__tests__/portfolio.test.js')).toBe(true);
    expect(isTestFile('pages/Portfolio.jsx')).toBe(false);
  });

  it('no longer has a shim to except, because the file is gone', () => {
    // Task 27.2's final stage B. This is asserted rather than only written down
    // in the docblock: if the shim ever came back, the rule this file enforces
    // would silently have an exemption again, and the assertions below — which
    // now demand ZERO declarations of `C` anywhere — would start failing with a
    // message about a competing palette rather than about a resurrected shim.
    expect(existsSync(path.join(SRC, DELETED_SHIM))).toBe(false);
  });

  it('proves the pipeline still fires on real bytes, so an all-clear cannot be vacuous', () => {
    // Non-vacuity, and the replacement for the shim's `export const C =`. Every
    // assertion in section 3 is now an emptiness assertion, so if path resolution
    // broke or the pattern stopped matching, `FOUND` would be empty and the guard
    // would pass over nothing while looking green.
    //
    // The anchor: take a file the scan really collected, read its real bytes, and
    // append one declaration. Finding it exercises read, comment-strip and match
    // together on production content — the same three steps `scan()` takes — so a
    // failure anywhere in the chain fails here instead of passing silently.
    const [anchorFile] = collect(SRC, SCANNED_EXTENSIONS)
      .filter((f) => !isTestFile(relToSrc(f)));
    expect(anchorFile, 'the scan collected no files at all').toBeTruthy();

    const real = readFileSync(anchorFile, 'utf8');
    expect(real.length, `${relToSrc(anchorFile)}: read no bytes`).toBeGreaterThan(0);

    // Counted against the same file WITHOUT the appended line, so the extra hit
    // is caused by the line rather than by something the file already held.
    const baseline = findLocalTokenDeclarations(real);
    const hits = findLocalTokenDeclarations(`${real}\nconst THEME = { canvas: 0 };\n`);

    expect(hits.length).toBe(baseline.length + 1);
    expect(hits.map((h) => h.name)).toContain('THEME');
  });
});

// ---------------------------------------------------------------------------
// 3. The rule
// ---------------------------------------------------------------------------

describe('no-local-tokens: no second token source', () => {
  it('lets NOTHING in src/ declare C — the shim was the last one and it is gone', () => {
    const declarers = [...new Set(FOUND.filter((h) => h.name === 'C').map((h) => h.relative))];

    expect(
      declarers,
      `\`const C =\` may not be declared anywhere in src/.\n\n`
        + `This assertion used to read \`toEqual([SHIM])\`: the derived compatibility shim at\n`
        + `components/ui-legacy/primitives.jsx was permitted to declare \`C\`, and everything\n`
        + `else imported it. Task 27.2's final stage B deleted the shim, so there is no\n`
        + `exemption left and no import to offer in its place.\n\n`
        + `Read \`token.*\` from src/design/tokens.js, or use a Tailwind token class.\n`
        + `A local \`C\` is a second token source — it is how #010608 and #0a0a0a became two\n`
        + `more canvas colours (design.md §1.1 G2). Requirement 1.1 allows ONE, and it is\n`
        + `src/styles/tokens.css.\n${list(declarers)}`,
    ).toEqual([]);
  });

  it('holds every file clear of all three declarations', () => {
    const violations = FOUND.filter(
      (h) => SCHEDULED_EXCEPTIONS[h.relative] !== h.name,
    ).map(describeHit);

    expect(
      violations,
      `Local token declarations found in src/.\n\n`
        + `\`const C\` / \`const COLORS\` / \`const THEME\` name a palette, and a palette\n`
        + `belongs in src/styles/tokens.css (design.md §15.1, Requirement 1.1). Use a\n`
        + `Tailwind token class, or \`token.*\` from src/design/tokens.js. The "outside the\n`
        + `shim" escape §15.1 allowed is spent: the shim went at task 27.2.\n${list(violations)}`,
    ).toEqual([]);
  });

  it('reads zero declarations in total, which is the state task 27.2 was aiming at', () => {
    // The whole rule in one line. `FOUND` is every hit from every non-test file
    // under `src/`, and there is nothing in it: no `C`, no `COLORS`, no `THEME`,
    // no exemption and no quarantine. §15.1 is satisfied structurally rather than
    // by a list of files that are allowed to break it.
    expect(FOUND.map(describeHit)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 4. The quarantine is exact, temporary and harmless
// ---------------------------------------------------------------------------

describe('no-local-tokens: the scheduled exceptions', () => {
  it('is empty, and cannot grow unnoticed', () => {
    // Widening this is the easy way to make the guard stop complaining, so the
    // list is pinned by value. Changing it means changing this assertion, which
    // means saying so in the diff.
    //
    // Empty as of task 16.2, which removed `pages/Portfolio.jsx`'s `const COLORS`.
    // §15.1's guard holds with no quarantine — and, since task 27.2's final stage B
    // deleted the shim, with no sanctioned declaration either. There is no `C`,
    // `COLORS` or `THEME` anywhere in `src/` at all.
    expect(SCHEDULED_EXCEPTIONS).toEqual({});
  });

  it('names only declarations that are really still there', () => {
    // A stale exception is a hole in the guard. When the M7 Portfolio migration
    // removes the array, this fails until the entry is deleted.
    const stale = Object.entries(SCHEDULED_EXCEPTIONS)
      .filter(([relative, name]) => !FOUND.some((h) => h.relative === relative && h.name === name))
      .map(([relative, name]) => `${relative} — const ${name} = is gone`);

    expect(
      stale,
      `These exceptions are no longer needed. The declaration they excuse has been\n`
        + `removed, so delete the entry from SCHEDULED_EXCEPTIONS in this file — an\n`
        + `exception nobody needs is a hole nobody is watching:\n${list(stale)}`,
    ).toEqual([]);
  });

  it('excuses only declarations that declare no colour of their own', () => {
    // This is what makes the quarantine safe rather than a loophole. An excused
    // declaration must be a list of references to the single source. The moment
    // one carries a literal it is a competing palette and this fails, whatever
    // the exception list says.
    const literal = /#[0-9a-fA-F]{3,8}\b|(?<![\w$-])(?:rgba?|hsla?)\s*\(/;

    for (const [relative, name] of Object.entries(SCHEDULED_EXCEPTIONS)) {
      const code = stripComments(readFileSync(path.join(SRC, relative), 'utf8'));
      const match = declarationPattern(name).exec(code);
      expect(match, `${relative}: const ${name} = not found`).toBeTruthy();

      // The declaration's initialiser, to the end of its statement.
      const from = match.index + match[0].length;
      const statement = code.slice(from, code.indexOf(';', from) + 1);

      expect(statement.trim().length, `${relative}: empty initialiser`).toBeGreaterThan(0);
      expect(
        literal.test(statement),
        `${relative}: \`const ${name} =\` now contains a colour literal:\n`
          + `  ${statement.trim()}\n\n`
          + `An excused declaration must reference the single token source, not declare a\n`
          + `colour. This is a competing palette — remove it from SCHEDULED_EXCEPTIONS and\n`
          + `fix the declaration.`,
      ).toBe(false);
      // …and it does reference the single source, so it is a projection, not an
      // invention. This read `/\bC\.[A-Za-z_$]/` while the shim existed — a future
      // exception would be a list of `token.*` reads, since that is the only
      // projection of `tokens.css` left after task 27.2.
      expect(statement, `${relative}: expected token.* references`).toMatch(
        /\btoken\.[A-Za-z_$]/,
      );
    }
  });
});
