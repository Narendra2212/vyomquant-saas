/**
 * `legacy-c-budget` — vyomquant-ui-redesign task 1.12.
 * Requirements 1.1, 1.3. design.md §3.4, §14.4, §15.1.
 *
 * ===========================================================================
 * WHAT THIS GUARD IS FOR
 * ===========================================================================
 * design.md §15.1: "The count of `C.` references per in-scope page is `<=` a
 * checked-in budget, which each migration step lowers. Reaches zero at M9,
 * after which the file is deleted."
 *
 * `C` is the derived compatibility shim from task 1.3 — every value in it is a
 * `token.*` reference, so it cannot disagree with `tokens.css`. It exists for
 * one reason: to let ~2050 existing `C.*` call sites keep resolving while the
 * pages are migrated one at a time, with no flag day (§3.4, §14.2). It is
 * scaffolding, and scaffolding that nobody counts is scaffolding that never
 * comes down.
 *
 * This file counts it. `legacy-c.budget.js` holds the committed per-file
 * numbers; this guard measures the tree and compares, and it fails in BOTH
 * directions:
 *
 *   over budget  — a new `C.` reference entered the tree. Use a token class, or
 *                  `token.*` from `src/design/tokens.js`, instead.
 *   under budget — references were removed and the budget was left where it was.
 *
 * The second half is the one that matters and the one that is easy to leave out.
 * Under a bare `<=` assertion a contributor could clear forty `C.` references
 * from `StrategyBuilder.jsx`, leave the budget at 148, and the next contributor
 * could put forty back with CI silent throughout. §14.4 does not promise that
 * legacy usage stays under a ceiling; it promises that "every step in between
 * strictly increases primitive coverage" and that the CI check "asserts
 * monotonic progress". Monotonic means the high-water mark comes down with the
 * water. Progress has to be *recorded* to count, not banked. That is the entire
 * mechanical content of the claim, and this is where it is cashed.
 *
 * The failure message states the number to write, so there is nothing to work
 * out — lowering the budget is a one-line edit in the same commit as the
 * migration.
 *
 * ===========================================================================
 * METHOD, AND HOW IT WAS VALIDATED
 * ===========================================================================
 * A reference is `C.` in a member-access position: `C.bg0`, `C.glow.profit`
 * (one reference, not two — the count is of accesses on the shim, not of
 * property hops).
 *
 * Three things must not be counted, and each of them was a live risk here
 * rather than a hypothetical:
 *
 *   1. `RC.foo`, `SVC.bar`, `_C.x` — a `C` that is the tail of a longer
 *      identifier. Handled by a `(?<![\w$])` lookbehind, the same one
 *      `no-local-tokens.test.js` uses. `\b` is NOT sufficient: `$` is not a
 *      word character, so `\bC\.` matches inside `$C.foo`.
 *   2. `C.` inside a comment. `styles/tokens.css` names `C.bg0`/`C.t1` eleven
 *      times in explanatory prose, `primitives.jsx`'s header discusses
 *      `C.space` and `C.radius`, and this docblock is doing the same thing
 *      several times over. A guard that counted documentation would penalise
 *      writing any of this down. `source-scan.js`'s `stripComments` removes
 *      them, length- and line-preserving, so reported line numbers still match
 *      the file on disk.
 *   3. `C.` inside a string literal. Nothing in `src/` does this today (the
 *      cross-check below proves it), but a message like `'C.glow is retired'`
 *      is not a reference and must not read as one.
 *
 * And one thing that MUST be counted, which is where a careless string strip
 * destroys the measurement: `` `1px solid ${C.border}` ``. Template-literal
 * interpolations are code, and in this codebase they are the *dominant* form of
 * legacy usage — `App.jsx` alone gets 3 of its 14 that way, `primitives.jsx`
 * dozens. So backticks are deliberately NOT masked, and only complete
 * same-line `'…'` / `"…"` spans are.
 *
 * Even that narrow mask needs the lookbehind. `source-scan.js`'s docblock
 * records what happens without it: an earlier attempt at these guards tokenised,
 * read the apostrophe in JSX text (`Don't`) as a string opener, swallowed the
 * rest of the file, and lost 33 of `StrategyBuilder.jsx`'s 148 references —
 * seeding a budget that was quietly 22% low. A prose apostrophe is preceded by
 * a letter; a real string opener is not. `MASKING_IS_NOT_LOSING_REFERENCES`
 * below then asserts, for every scanned file, that masking removes nothing —
 * so if the mask ever does start eating a real reference, that is a named
 * failure rather than a number silently drifting down.
 *
 * The self-tests in "the counting method" pin all of the above, and
 * "the spot check" reproduces `App.jsx`'s 14 by hand, occurrence by occurrence.
 * They are not decoration: a silently broken pattern makes this whole guard
 * pass vacuously and turns every number in the budget file into fiction.
 *
 * ===========================================================================
 * KNOWN BLIND SPOT: DYNAMIC ACCESS
 * ===========================================================================
 * `C[key]` is invisible to a `C.` count. There is exactly one in the tree,
 * `primitives.jsx:709`'s `C[glowColor] || C.accent` in the legacy `Card`, and it
 * is inside the shim itself — so it does not distort any page's migration
 * number, and it disappears when the shim does at task 27.2. Recorded rather
 * than patched around: a pattern that tried to catch dynamic access would have
 * to guess at intent, and this budget's job is to be unarguable.
 *
 * ===========================================================================
 * MEASURED STATE OF THE TREE (task 3.1–3.3 have landed since the seed)
 * ===========================================================================
 * The budget was seeded when task 1.12 landed. Tasks 3.1, 3.2 and 3.3 have
 * since edited `App.jsx`, `Sidebar.jsx`, `TopBar.jsx`, `Dashboard.jsx`,
 * `Strategies.jsx`, `StrategyDetail.jsx`, `Portfolio.jsx`, `StrategyBuilder.jsx`,
 * `ui/Card.jsx` and the five `components/builder/*` files.
 *
 * Every one of those edits swapped a dead `className` for a live one or moved a
 * font declaration; none added or removed a `C.` reference. All 32 committed
 * numbers reproduce exactly, so this guard passes against the current tree with
 * the budget unchanged. That is worth stating explicitly, because "the guard
 * passed" and "the guard was measured against a tree it was seeded from" are
 * different claims and only the first one is true here.
 */

import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, it, expect } from 'vitest';

import { LEGACY_C_BUDGET, SHIM_PATH } from './legacy-c.budget.js';
import { SRC, collect, isTestFile, list, relToSrc, stripComments } from './source-scan.js';

const BUDGET_FILE = 'tests/unit/guards/legacy-c.budget.js';

/**
 * `C` is a JavaScript identifier, so only JavaScript can reference it. `.ts` and
 * `.tsx` are included alongside `.js`/`.jsx` — the budget file's header says
 * `.js` and `.jsx` because those were the only source extensions when it was
 * seeded, and `src/` now holds two TypeScript files (`api/typed-client.ts`,
 * `types/api.types.ts`). Neither references the shim, so this widening changes
 * no number today; it means a future `.ts` file cannot read from the shim
 * outside the budget's view. Same list as `no-local-tokens.test.js`.
 */
const SCANNED_EXTENSIONS = Object.freeze(['.js', '.jsx', '.ts', '.tsx']);

// ---------------------------------------------------------------------------
// Counting
// ---------------------------------------------------------------------------

/**
 * `C.` in a member-access position.
 *
 * The lookbehind rejects a `C` that ends a longer identifier (`RC.`, `SVC.`,
 * `_C.`, `$C.`). The lookahead requires an identifier to follow, so a bare `C`
 * passed as a value (`<Foo tokens={C} />`) is not a reference and `C .foo` — not
 * a form anything here writes — is not either.
 *
 * `.` is deliberately absent from the lookbehind: `{...C.space}` is a real
 * reference. The cost is that a hypothetical `obj.C.x` would count; no such
 * form exists in `src/`.
 */
const LEGACY_C = /(?<![\w$])C\.(?=[A-Za-z_$])/g;

/**
 * A complete same-line `'…'` / `"…"` span whose opening quote could actually
 * open a string.
 *
 * The lookbehind is what keeps the apostrophe in `Don't` from opening a span and
 * swallowing the real references after it — see the METHOD note. Backticks are
 * absent on purpose: `${C.x}` is code.
 */
const STRING_SPAN = /(?<![\w$])'(?:[^'\\\n]|\\.)*'|(?<![\w$])"(?:[^"\\\n]|\\.)*"/g;

/** Blank string spans, preserving length and line breaks. */
const maskStrings = (code) => code.replace(STRING_SPAN, (m) => ' '.repeat(m.length));

/**
 * Every shim reference in `source`, as `{ line }`, in file order.
 *
 * `excludeStrings: false` skips the string mask. Only the cross-check below uses
 * it — it is how "the mask is not losing references" is asserted rather than
 * assumed.
 */
export function findLegacyC(source, { excludeStrings = true } = {}) {
  const stripped = stripComments(source);
  const code = excludeStrings ? maskStrings(stripped) : stripped;
  return [...code.matchAll(LEGACY_C)].map((m) => ({
    line: code.slice(0, m.index).split('\n').length,
  }));
}

/** How many shim references a source file contains. */
export function countLegacyC(source, options) {
  return findLegacyC(source, options).length;
}

// ---------------------------------------------------------------------------
// Scanning
// ---------------------------------------------------------------------------

/** `[{ relative, count, unmaskedCount }]` for every JavaScript file in `src/`. */
function scan() {
  const files = [];
  for (const full of collect(SRC, SCANNED_EXTENSIONS)) {
    const relative = relToSrc(full);
    if (isTestFile(relative)) continue;
    const source = readFileSync(full, 'utf8');
    files.push({
      relative,
      count: countLegacyC(source),
      unmaskedCount: countLegacyC(source, { excludeStrings: false }),
    });
  }
  return files.sort((a, b) => a.relative.localeCompare(b.relative));
}

const SCANNED = scan();

const budgeted = (relative) => relative in LEGACY_C_BUDGET;

// ---------------------------------------------------------------------------
// 1. The counting method itself
// ---------------------------------------------------------------------------

describe('legacy-c-budget: the counting method', () => {
  it('counts a member access on the shim', () => {
    expect(countLegacyC('style={{ background: C.bg0 }}')).toBe(1);
    expect(countLegacyC('color: C.t1, border: C.border, background: C.bg2')).toBe(3);
    expect(countLegacyC('padding: C.space.md, borderRadius: C.radius.lg')).toBe(2);
  });

  it('counts a nested access once, not once per property hop', () => {
    // `C.glow.profit` is one reference to the shim. Counting the hops would
    // make a budget that moves when nothing about the migration has.
    expect(countLegacyC('boxShadow: C.glow.profit')).toBe(1);
    expect(countLegacyC('background: C.gradient.card')).toBe(1);
  });

  it('counts references inside template-literal interpolations', () => {
    // The dominant legacy form in this codebase, and the one a naive string
    // strip destroys. App.jsx gets 3 of its 14 this way.
    expect(countLegacyC('border: `1px solid ${C.border}`')).toBe(1);
    expect(countLegacyC('background: `${C.green}12`')).toBe(1);
    expect(countLegacyC('`1px solid ${isFocused ? C.accent : C.border}`')).toBe(2);
  });

  it('does not count a C that merely ends a longer identifier', () => {
    // The named false positives. `\bC\.` would let `$C.` through.
    expect(countLegacyC('RC.foo')).toBe(0);
    expect(countLegacyC('SVC.bar')).toBe(0);
    expect(countLegacyC('const _C = {}; _C.x')).toBe(0);
    expect(countLegacyC('$C.y')).toBe(0);
    expect(countLegacyC('apiRC.get()')).toBe(0);
    // Real symbols in this tree that end in C and are read as members.
    expect(countLegacyC('BTC.price + ETHUSDC.qty')).toBe(0);
  });

  it('does not count a bare C used as a value', () => {
    expect(countLegacyC('<Palette tokens={C} />')).toBe(0);
    expect(countLegacyC('export { C };')).toBe(0);
    expect(countLegacyC('const { bg0 } = C;')).toBe(0);
  });

  it('does not count a reference written in a comment', () => {
    // Without this, tokens.css's eleven explanatory mentions, primitives.jsx's
    // header, and this file's own docblock would all register.
    expect(countLegacyC('// C.glow.* is now none')).toBe(0);
    expect(countLegacyC('/* was C.purple, now C.neutral */')).toBe(0);
    expect(countLegacyC(['/**', ' * `C.space` stays numeric.', ' */'].join('\n'))).toBe(0);
  });

  it('still counts a reference on a line that also carries a comment', () => {
    expect(countLegacyC('color: C.t3, // muted label')).toBe(1);
    expect(countLegacyC('background: C.bg2 /* surface */, color: C.t1')).toBe(2);
  });

  it('does not count a reference inside a quoted string', () => {
    expect(countLegacyC('const msg = "C.glow was retired in M1";')).toBe(0);
    expect(countLegacyC("throw new Error('C.purple is gone');")).toBe(0);
    // …and the surrounding code is still measured.
    expect(countLegacyC('log("C.gold"); style={{ color: C.gold }}')).toBe(1);
  });

  it('is not confused by an apostrophe in JSX text', () => {
    // The failure mode that seeded a budget 22% low. The apostrophe in `Don't`
    // is preceded by a letter, so it cannot open a string span and cannot
    // swallow the reference that follows it.
    expect(countLegacyC("<p style={{ color: C.t2 }}>Don't stop</p>")).toBe(1);
    expect(countLegacyC("<p>Don't</p><b style={{ color: C.red }}>It's live</b>")).toBe(1);
    expect(countLegacyC("<span>users' funds</span><i style={{ color: C.t3 }} />")).toBe(1);
  });

  it('handles an escaped quote inside a string', () => {
    expect(countLegacyC(`const m = 'can\\'t reach C.bg0'; color: C.t1`)).toBe(1);
  });

  it('reports the line each reference is on', () => {
    const source = ['const a = 1;', 'color: C.t1,', '', 'background: C.bg0,'].join('\n');
    expect(findLegacyC(source)).toEqual([{ line: 2 }, { line: 4 }]);
  });
});

// ---------------------------------------------------------------------------
// 2. The spot check — one real file, counted by hand
// ---------------------------------------------------------------------------

describe('legacy-c-budget: the spot check', () => {
  it("reproduces App.jsx's 14 references occurrence by occurrence", () => {
    // Hand-verified against the file, so the counting method is pinned to
    // reality and not only to synthetic fixtures. `App.jsx` is a good choice:
    // small enough to enumerate, touched by task 3.3, and it exercises both the
    // template-literal form and the comment exclusion (task 3.3 added three
    // docblocks to it that name `--font-mono`).
    //
    // ResetPassword:  78 C.bg0 · 79 C.bg2, ${C.border} · 80 C.t1
    //                 84 C.green, ${C.green} · 102 C.red                    = 7
    // AdminGuard:    165 C.bg0 · 170 C.t3 · 176 C.bg0 · 177 C.red
    //                178 C.t1 · 181 C.t3                                    = 6
    // AppShell:      297 C.bg0                                              = 1
    const source = readFileSync(path.join(SRC, 'App.jsx'), 'utf8');
    const lines = findLegacyC(source).map((r) => r.line);

    expect(lines).toEqual([78, 79, 79, 80, 84, 84, 102, 165, 170, 176, 177, 178, 181, 297]);
    expect(lines).toHaveLength(14);
    expect(LEGACY_C_BUDGET['App.jsx']).toBe(14);
  });
});

// ---------------------------------------------------------------------------
// 3. Scope — and non-vacuity
// ---------------------------------------------------------------------------

describe('legacy-c-budget: scope', () => {
  it('scans all of src/, not just the in-scope pages', () => {
    // The shim cannot be deleted at task 27.2 while any file still reads from
    // it, so a deferred page's references are as much a blocker as a page in
    // the redesign's own scope.
    expect(SCANNED.length).toBeGreaterThan(100);
    for (const dir of ['pages', 'components', 'lib']) {
      expect(
        SCANNED.some((f) => f.relative.startsWith(`${dir}/`)),
        `src/${dir} is not being scanned`,
      ).toBe(true);
    }
    expect(SCANNED.map((f) => f.relative)).toContain('App.jsx');
  });

  it('leaves test files out', () => {
    expect(SCANNED.some((f) => isTestFile(f.relative))).toBe(false);
    expect(isTestFile('components/ui-legacy/__tests__/legacyTokenShim.test.js')).toBe(true);
    expect(isTestFile('pages/StrategyBuilder.jsx')).toBe(false);
  });

  it('actually finds the shim, so a silent non-run cannot look green', () => {
    // Non-vacuity. If path resolution broke — the `import.meta.url` trap
    // source-scan.js documents — or the pattern stopped matching, every count
    // would be 0, every "at or below budget" assertion would pass over nothing,
    // and only the "under budget" direction would object. This is the fixed
    // point: the shim defines `C` and reads its own keys ~178 times, so a
    // working scan cannot report a small number here.
    const shim = SCANNED.find((f) => f.relative === SHIM_PATH);

    expect(existsSync(path.join(SRC, SHIM_PATH)), `${SHIM_PATH} is missing`).toBe(true);
    expect(shim, `${SHIM_PATH} was not reached by the scan`).toBeDefined();
    expect(shim.count).toBeGreaterThan(100);
    expect(LEGACY_C_BUDGET[SHIM_PATH]).toBeGreaterThan(100);
  });

  it('finds references across many files, not just the shim', () => {
    // A pattern that only ever fires inside primitives.jsx would satisfy the
    // check above and still be broken for every page.
    const carriers = SCANNED.filter((f) => f.count > 0);
    expect(carriers.length).toBeGreaterThan(20);
    expect(carriers.reduce((n, f) => n + f.count, 0)).toBeGreaterThan(1500);
  });

  it('does not lose references to the string mask', () => {
    // MASKING_IS_NOT_LOSING_REFERENCES. Excluding strings can only ever remove
    // matches, which makes it the one part of the method that could quietly
    // undercount — exactly the failure that seeded a 22%-low budget. Today no
    // file in src/ has a `C.` inside a quoted string, so the masked and unmasked
    // counts agree everywhere. If a genuine `C.` in a string ever appears this
    // will fail; the fix is to confirm it really is prose and add it here, not
    // to drop the mask.
    const diverged = SCANNED.filter((f) => f.count !== f.unmaskedCount).map(
      (f) => `${f.relative} — ${f.unmaskedCount} without the string mask, ${f.count} with it`,
    );

    expect(
      diverged,
      `The string mask changed a count. Either a reference is genuinely written inside a\n`
        + `string literal, or the mask is swallowing code — check for an apostrophe in JSX\n`
        + `text on the same line as a reference before touching any budget number:\n`
        + `${list(diverged)}`,
    ).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 4. The budget is a ratchet
// ---------------------------------------------------------------------------

describe('legacy-c-budget: the decreasing budget', () => {
  it('is well formed', () => {
    expect(Object.keys(LEGACY_C_BUDGET).length).toBeGreaterThan(0);
    for (const [relative, budget] of Object.entries(LEGACY_C_BUDGET)) {
      expect(Number.isInteger(budget), `${relative}: ${budget} is not an integer`).toBe(true);
      expect(budget, `${relative}: ${budget} is negative`).toBeGreaterThanOrEqual(0);
      expect(relative, `${relative} must be relative to src/ with forward slashes`).toMatch(
        /^[\w.-]+(?:\/[\w.-]+)*$/,
      );
    }
    expect(Object.isFrozen(LEGACY_C_BUDGET)).toBe(true);
  });

  it('names only files that still exist', () => {
    const gone = Object.keys(LEGACY_C_BUDGET).filter(
      (relative) => !existsSync(path.join(SRC, relative)),
    );
    expect(
      gone,
      `These budget entries name files that are no longer in src/. A deleted file takes its\n`
        + `entry with it — remove them from ${BUDGET_FILE}:\n${list(gone)}`,
    ).toEqual([]);
  });

  it('accounts for every file that still references the shim', () => {
    const unbudgeted = SCANNED.filter((f) => f.count > 0 && !budgeted(f.relative)).map(
      (f) => `${f.relative} — ${f.count} reference(s)`,
    );

    expect(
      unbudgeted,
      `New \`C.\` references in files with no budget entry.\n\n`
        + `\`C\` is scaffolding on its way out (design.md §3.4). Use a Tailwind token class\n`
        + `from src/styles/tokens.css, or \`token.*\` from src/design/tokens.js. If this file\n`
        + `genuinely has to read the shim for now, add an entry to ${BUDGET_FILE}\n`
        + `with a note naming the task that clears it.\n${list(unbudgeted)}`,
    ).toEqual([]);
  });

  it('holds every file at or below its budget', () => {
    const over = SCANNED.filter(
      (f) => budgeted(f.relative) && f.count > LEGACY_C_BUDGET[f.relative],
    ).map(
      (f) =>
        `${f.relative} — budget ${LEGACY_C_BUDGET[f.relative]}, actual ${f.count} `
        + `(+${f.count - LEGACY_C_BUDGET[f.relative]})`,
    );

    expect(
      over,
      `\`C.\` references were added to files that are supposed to be shrinking.\n\n`
        + `Replace them with tokens. Raising a budget in ${BUDGET_FILE} reverses\n`
        + `design.md §14.4's monotonic-progress rule and needs a reason in the PR.\n`
        + `${list(over)}`,
    ).toEqual([]);
  });

  it('requires progress to be recorded, not banked', () => {
    const under = SCANNED.filter(
      (f) => budgeted(f.relative) && f.count < LEGACY_C_BUDGET[f.relative],
    ).map(
      (f) =>
        `${f.relative} — lower the committed budget from `
        + `${LEGACY_C_BUDGET[f.relative]} to ${f.count}`,
    );

    expect(
      under,
      `These files now hold fewer \`C.\` references than their committed budget. Good — but\n`
        + `the budget has to come down with them, in this commit, or the headroom stays open\n`
        + `for the references to creep back in unnoticed, and §14.4's monotonic-progress\n`
        + `claim stops being checkable. Edit ${BUDGET_FILE}:\n${list(under)}`,
    ).toEqual([]);
  });

  it('has no stale entry sitting at a count the file no longer has', () => {
    // The two directions above, restated as the single thing a reader of the
    // budget file cares about: every committed number is the truth about the
    // tree right now. This is the assertion that makes the budget readable as
    // documentation rather than as a ceiling of unknown slack.
    const actual = new Map(SCANNED.map((f) => [f.relative, f.count]));
    const stale = Object.entries(LEGACY_C_BUDGET)
      .filter(([relative, budget]) => actual.get(relative) !== budget)
      .map(([relative, budget]) => `${relative} — says ${budget}, is ${actual.get(relative) ?? 0}`);

    expect(
      stale,
      `Committed budget numbers that no longer describe the tree. Correct them in\n`
        + `${BUDGET_FILE} — a number nobody has checked is a number nobody can\n`
        + `rely on:\n${list(stale)}`,
    ).toEqual([]);
  });

  it('has an entry for every file it claims to track, and no strays', () => {
    const scannedNames = new Set(SCANNED.map((f) => f.relative));
    const strays = Object.keys(LEGACY_C_BUDGET).filter(
      (relative) => !scannedNames.has(relative),
    );
    expect(
      strays,
      `These budget entries name files that exist but are outside this guard's scan\n`
        + `(all of src/, excluding tests, restricted to ${SCANNED_EXTENSIONS.join('/')}).\n`
        + `Remove them from ${BUDGET_FILE} or widen SCANNED_EXTENSIONS deliberately:\n`
        + `${list(strays)}`,
    ).toEqual([]);
  });
});
