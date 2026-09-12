/**
 * `dead-tailwind` — vyomquant-ui-redesign task 3.4. Requirement 1.1.
 * design.md §1.2, §15.1.
 *
 * ===========================================================================
 * WHAT THIS GUARD IS FOR
 * ===========================================================================
 * design.md §15.1: "Every `className` token used in `src/` resolves in the built
 * CSS. This is the test that would have caught §1.2. Run against
 * `dist/assets/*.css` after build."
 *
 * §1.2 is the failure it is named for. `tailwind.config.js` declared a typography
 * scale and two glow shadows. `package.json` pins Tailwind v4 with
 * `@tailwindcss/postcss`, `index.css` has no `@config`, and no `@config` appears
 * anywhere in `src/` — so under v4 that file was never loaded. `text-micro`,
 * `text-caption`, `text-caption-sm`, `text-body`, `text-body-sm`, `text-body-lg`,
 * `text-heading*`, `shadow-glow` and `shadow-glow-green` compiled to nothing, and
 * 143 elements across seven files rendered at the inherited font size for as long
 * as that config sat there. Nothing failed. Nothing warned. The classes were
 * spelled correctly, imported nothing, and simply did not exist.
 *
 * A class name is a string. That is the whole problem: there is no import to
 * break, no symbol to resolve, no type to check. The only way a dead class
 * becomes visible is by comparing what the source asks for against what the
 * build actually produced — which is what this file does.
 *
 * It is enforceable now and was not before, because task 3.1 re-pointed all 143
 * onto the `tokens.css` scale (`text-micro`, `text-small`, `text-body`,
 * `text-title`, `text-section`, `text-page`, `text-figure`) and removed
 * `ui/Card.jsx`'s inert `hover:shadow-glow`. Landing it before 3.1 would have
 * meant a guard that failed 143 times on day one.
 *
 * ===========================================================================
 * IT NEEDS A BUILD, AND SKIPS RATHER THAN FAILS WITHOUT ONE
 * ===========================================================================
 * The comparison is against build *output*, so this is the one guard here that
 * cannot run from a clean checkout. `npm test` must not silently require
 * `npm run build`, and a missing artefact is not a violation of Requirement 1.1
 * — so when `dist/assets/*.css` is absent the suite skips and says why, in
 * words, naming the command to run. It does not pass quietly (which would let a
 * CI job that forgot to build report green on a guard that checked nothing) and
 * it does not fail (which would make `npm test` useless locally).
 *
 * `.github/workflows/01-pr-check.yml`'s `frontend-tests` job runs "Build
 * frontend" (`npm run build`) immediately before "Run frontend unit tests"
 * (`npx vitest --run`), so CI always gets the real check. A developer running
 * the tests alone gets the notice instead, and each skipped test carries the
 * one-line reason with it, so `6 skipped` is never an unexplained number.
 *
 * ===========================================================================
 * HOW CLASS TOKENS ARE EXTRACTED, AND WHAT THE EXTRACTION CANNOT SEE
 * ===========================================================================
 * Two kinds of site are read:
 *
 *   1. `className=` — the JSX attribute and the prop, in all three forms this
 *      codebase writes: `className="a b c"`, `` className={`a ${cond ? 'b' :
 *      'c'}`} `` and `className={cond ? 'a' : 'b'}`.
 *   2. `const <name>Class… =` — declarations whose identifier ends in
 *      `Class`/`Classes`/`ClassName`. That idiom holds real class lists here and
 *      the §1.2 classes were in them: `AssetSelector.jsx`'s `INPUT_CLASS`,
 *      `FILTER_CLASS` and `LINK_CLASS`, `TimeframeSelector.jsx`'s `SELECT_CLASS`,
 *      `ParameterForm.jsx`'s `CONTROL_CLASS`, `ui/Card.jsx` and
 *      `ui/Button.jsx`'s `combinedClass`. A guard that read only `className=`
 *      would have missed the very files task 3.1 had to fix.
 *
 * Inside a site, every string literal and every template-literal static chunk
 * contributes its text; everything else contributes a marker. A token that
 * touches a marker is DROPPED, not failed, because it may be a fragment rather
 * than a class: `` `text-${size}` `` yields `text-`, which is not a class name
 * and must never be reported as a dead one. Boundaries are only treated as
 * fusing where the static text actually lacks whitespace there, so
 * `` `mt-1 text-micro ${…}` `` keeps both of its real tokens. `'a ' + 'b'`
 * between two literals is plain concatenation and is joined verbatim.
 *
 * Not every literal at such a site is class text, and the position it sits in is
 * what decides: `userOS === 'windows'` is a comparison, `fmt('flex', x)` is a call
 * argument, and `SIZE_CLASS[known ? size : 'sm']` is a lookup key — a *ternary
 * branch*, which is class text anywhere else, inside a subscript, which is never
 * class text. `[`-depth is tracked for that last one, because looking one token
 * backwards cannot see the `SIZE_CLASS[` that changes the answer. See
 * `flattenClassExpression`, which also records why array literals get no
 * exemption from it.
 *
 * WHAT IT CANNOT SEE, stated plainly because a guard that overstates its reach
 * is worse than one that admits a gap:
 *
 *   * Computed names — `` `text-${size}` ``, `'bg-' + tone`, `variants[key]`,
 *     `styles.card`. The dynamic part is unknowable without evaluating the
 *     component, and the surrounding fragment is deliberately discarded.
 *   * Class lists reached through a name this file does not recognise. A list in
 *     `const base = '…'` or in an object literal (`ui/Badge.jsx`'s `variants` and
 *     `dotColors` maps, read as `variants[activeVariant]`) is invisible. Widening
 *     to "every string that looks like a class list" was rejected: it would start
 *     failing on prose, ids and API paths, and a guard that cries wolf gets
 *     deleted.
 *   * Anything inside `[` … `]`. A subscript's contents are a key, not a class —
 *     `SIZE_CLASS[known ? size : 'sm']` asks for the class list *at* `sm`, and
 *     `sm` is not a class — so no literal at `[`-depth is read. Array literals
 *     are inside that blind spot too, deliberately: an element of
 *     `['a', 'b'].join(' ')` was already unreadable (it follows `[` or `,`), and
 *     the branch forms this rule newly hides, `[cond ? 'a' : 'b'].join(' ')` and
 *     `[base, cond && 'p-2'].filter(Boolean)`, appear nowhere in `src/`. The
 *     alternative — guessing array-literal from subscript by the token before the
 *     `[` — trades a lost harvest for a possible false failure, which is the
 *     wrong way round here.
 *   * Classes applied outside `src/` — `index.html`, and anything a library adds
 *     at runtime.
 *
 * Every one of those is an under-claim: a class it cannot see is a class it will
 * not complain about. That direction is deliberate. A false failure here costs a
 * contributor an afternoon and gets the guard disabled; a missed dead class costs
 * what §1.2 cost, which this guard at least narrows.
 *
 * ===========================================================================
 * HOW "RESOLVES" IS DECIDED
 * ===========================================================================
 * Every `.css` under `dist/assets/` is read — the app stylesheet and
 * `vendor-reactflow-*.css`, so React Flow's own classes resolve as themselves.
 * Class names are harvested from *selector* positions only, brace-depth tracked,
 * at-rule preludes skipped, so `padding:.75rem` does not register as a class
 * called `75rem`. CSS escapes are then undone, which is what makes the awkward
 * Tailwind v4 names comparable: `.hover\:text-x:hover` → `hover:text-x`,
 * `.min-w-\[220px\]` → `min-w-[220px]`, `.w-1\.5` → `w-1.5`,
 * `.border-accent-cyan\/30` → `border-accent-cyan/30`.
 *
 * `THE_HARVEST_IS_HONEST` pins both directions: four classes known to be live
 * must be found, and the §1.2 classes known to be dead must NOT be, so a
 * harvester that accidentally matched everything cannot make this guard vacuous.
 *
 * ===========================================================================
 * index.css's TWELVE `@utility` BLOCKS — AND A FINDING ABOUT FIVE OF THEM
 * ===========================================================================
 * `index.css` defines twelve classes by hand: `section-container`,
 * `section-inner`, `glass-nav`, `btn-primary`, `btn-ghost`, `btn-gold`,
 * `card-surface`, `card-elevated`, `text-gradient-cyan`, `terminal-frame`,
 * `scrollbar-hide`, `badge-status`. They are not Tailwind utilities, but they do
 * not need an allowlist either: `@utility` is on-demand in v4, so a used one is
 * emitted and resolves like anything else. The seven with call sites in `src/`
 * (`section-container`, `section-inner`, `btn-primary`, `btn-ghost`,
 * `card-surface`, `text-gradient-cyan`, `terminal-frame`) all resolve, and
 * `THE_TWELVE_UTILITIES` below asserts that rather than excusing it.
 *
 * design.md flagged five as emitting nothing today — `glass-nav`, `btn-gold`,
 * `card-elevated`, `scrollbar-hide`, `badge-status`. Measured against the current
 * build that is now **two**, and the reason the other three came back is worth
 * writing down:
 *
 *   * `scrollbar-hide` and `badge-status` still emit nothing. Neither has a
 *     `className` call site anywhere in `src/`, so this guard cannot surface
 *     them — an unused `@utility` correctly compiles to nothing, and that is not
 *     a §1.2-class defect. Reported, not allowlisted: they are dead definitions
 *     awaiting either a call site or deletion.
 *   * `glass-nav`, `btn-gold` and `card-elevated` DO appear in the current build,
 *     and they have no call site either. They are emitted because Tailwind v4's
 *     content scanner is a text scanner over the whole project, and
 *     `no-colour-literals.test.js`'s docblock happens to name all three while
 *     explaining why index.css is out of that guard's scope. A comment in a test
 *     file is materialising CSS in the production bundle.
 *
 * That last point is also this guard's own ceiling, so it is recorded here rather
 * than buried: the built stylesheet is a slightly *more* permissive oracle than
 * "reachable from `src/`", because the scanner also reads tests, comments and
 * docs. A class that is dead in the app but mentioned in a comment somewhere will
 * resolve. Narrowing that would mean reimplementing Tailwind's candidate
 * extraction, which is a worse trade than the residual gap.
 *
 * ===========================================================================
 * FINDING: NINE LIVE DEAD CLASSES — EIGHT NOW FIXED, ONE DELIBERATE
 * ===========================================================================
 * Run against the build the day it landed, this guard found nine class names that
 * `src/` used and the stylesheet did not contain. They were real, they were in
 * production, and none was introduced by task 3.4. Eight have since been repaired
 * at source; the ninth is dead on purpose and stays. `KNOWN_DEAD_CLASSES` below
 * now holds only that one, and carries the same account at more length. The short
 * version, kept because what was wrong outlives the fix:
 *
 *   * FIXED — seven in `pages/StrategyMarketplace.jsx` were arbitrary-value
 *     classes with the opacity slash missing: `bg-[#00D4FF]5` where
 *     `bg-[#00D4FF]/5` was meant, `hover:bg-[#26A69A]90` where `.../90` was, and
 *     five more over eight occurrences. `components/ui/Badge.jsx` writes the
 *     identical values correctly, which is what made these typos rather than a
 *     house style. Until they were fixed the hero glow, both notice banners' wash
 *     and the subscribe and clone buttons' hover state all rendered nothing, in
 *     the file design.md's §1.1 table calls "already fully Tailwind". Repaired by
 *     inserting the missing `/` at each occurrence and nothing else — the hex
 *     literals are untouched, so `no-colour-literals`' budget for the file is
 *     unchanged and task 22.1's retoken still owns replacing them.
 *   * FIXED — `bg-bg-base` in `components/builder/AssetSelector.jsx` named a
 *     token that does not exist (the aliases are `bg-primary`, `bg-surface`,
 *     `bg-elevated`, `bg-3`), so the asset dropdown's active row had no
 *     highlight. Keyboard navigation through that list was invisible, which made
 *     it an accessibility defect rather than a cosmetic one. Repaired as
 *     `bg-bg-3` — the listbox itself is `bg-bg-elevated`, so that alias would
 *     have been a second invisible highlight; see `KNOWN_DEAD_CLASSES` for the
 *     measured contrast and what it still leaves for the design pass.
 *   * `animate-pulse-glow` in `components/landing/FinalCTA.jsx` is dead *on
 *     purpose*: `tokens.css` records that `--animate-pulse-glow` was not carried
 *     forward because Requirement 1.5 retires coloured glows. The animation is
 *     correctly gone; only the class name was left behind. It stays quarantined
 *     until a landing-page pass, which is out of scope here (§17.2).
 *
 * The quarantine is pinned by value and asserted non-stale in both directions, so
 * it cannot grow quietly and cannot outlive the fixes — which is why shrinking it
 * to one was part of the same change that fixed the eight, not a follow-up.
 */

import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';

import { describe, it, expect } from 'vitest';

import {
  SRC,
  TERMINAL_ROOT,
  collect,
  isTestFile,
  list,
  relToSrc,
  stripComments,
} from './source-scan.js';

// ---------------------------------------------------------------------------
// Build output
// ---------------------------------------------------------------------------

const DIST_ASSETS = path.join(TERMINAL_ROOT, 'dist', 'assets');

/** Every built stylesheet, sorted. Empty when there is no build. */
const STYLESHEETS = existsSync(DIST_ASSETS)
  ? readdirSync(DIST_ASSETS)
    .filter((f) => f.endsWith('.css'))
    .sort()
    .map((f) => path.join(DIST_ASSETS, f))
  : [];

const HAS_BUILD = STYLESHEETS.length > 0;

const SKIP_NOTICE =
  'dead-tailwind: SKIPPED — no built stylesheet at dist/assets/*.css.\n'
  + '  This guard compares the class names src/ asks for against the CSS the build\n'
  + '  actually produced, so it has nothing to compare against until there is a build.\n'
  + '  Run `npm run build` in algo22-terminal/ and re-run the tests to enforce it.\n'
  + '  Skipping rather than failing is deliberate: a missing build artefact is not a\n'
  + '  Requirement 1.1 violation. CI builds first — 01-pr-check.yml\'s frontend-tests\n'
  + '  job runs `npm run build` before `npx vitest --run` — so it is enforced there.';

/** The one-line form, attached to each skipped test so the reason travels with it. */
const SKIP_NOTE = 'no dist/assets/*.css — run `npm run build` first';

/**
 * A test that only means anything against build output.
 *
 * Without a build it is *skipped carrying the reason*, rather than silently
 * absent. `describe.runIf` alone would drop the tests with no explanation, and a
 * skip nobody can account for is indistinguishable from a guard that was quietly
 * removed.
 */
const needsBuild = (name, assertion) =>
  it(name, (ctx) => {
    if (!HAS_BUILD) ctx.skip(SKIP_NOTE);
    assertion();
  });



// ---------------------------------------------------------------------------
// Harvesting class names out of built CSS
// ---------------------------------------------------------------------------

/**
 * A class in a selector: `.` then an identifier, where `\x` is one escaped
 * character and `\26 ` is a hex escape. Stops at the first unescaped character
 * that cannot be in an identifier, so `.hover\:text-x:hover` yields
 * `hover\:text-x` and not the trailing pseudo-class.
 */
const CLASS_IN_SELECTOR = /\.((?:[A-Za-z0-9_-]|\\[0-9a-fA-F]{1,6}[ \t\n]?|\\[^\n])+)/g;

/** Undo CSS identifier escaping, so a harvested name reads as it does in JSX. */
function unescapeCssIdent(raw) {
  return raw.replace(/\\([0-9a-fA-F]{1,6})[ \t\n]?|\\([^\n])/g, (_m, hex, chr) =>
    (hex === undefined ? chr : String.fromCodePoint(parseInt(hex, 16))));
}

/** Index just past a CSS string starting at `i` (the quote). */
function skipCssString(css, i) {
  const quote = css[i];
  let j = i + 1;
  while (j < css.length) {
    if (css[j] === '\\') { j += 2; continue; }
    if (css[j] === quote) return j + 1;
    j += 1;
  }
  return j;
}

/**
 * Every class name defined by `css`.
 *
 * Only text in a *selector* position — between a `{`/`}`/`;` and the next `{` —
 * is examined, and preludes beginning `@` are skipped. Without that, a
 * declaration such as `padding:.75rem` contributes a class called `75rem`, and a
 * resolution set full of phantoms is a guard that never fails.
 */
export function harvestClassNames(css) {
  const names = new Set();
  let prelude = '';
  let i = 0;

  while (i < css.length) {
    const ch = css[i];
    if (ch === '"' || ch === "'") {
      const end = skipCssString(css, i);
      prelude += css.slice(i, end);
      i = end;
      continue;
    }
    if (ch === '\\') {
      prelude += css.slice(i, i + 2);
      i += 2;
      continue;
    }
    if (ch === '{') {
      if (!prelude.trimStart().startsWith('@')) {
        for (const m of prelude.matchAll(CLASS_IN_SELECTOR)) names.add(unescapeCssIdent(m[1]));
      }
      prelude = '';
      i += 1;
      continue;
    }
    if (ch === '}' || ch === ';') {
      prelude = '';
      i += 1;
      continue;
    }
    prelude += ch;
    i += 1;
  }

  return names;
}

/** Union of the class names in every built stylesheet. */
const RESOLVED = new Set(
  STYLESHEETS.flatMap((file) => [...harvestClassNames(readFileSync(file, 'utf8'))]),
);

// ---------------------------------------------------------------------------
// Extracting class tokens out of source
// ---------------------------------------------------------------------------

/**
 * Marks a position where a dynamic value joins the class string. A token
 * containing one is unknowable, so it is discarded rather than reported.
 */
const HOLE = '\u0000';

/** A quoted string starting at `i`. Escapes become a space: never class text. */
function readQuoted(src, i) {
  const quote = src[i];
  let text = '';
  let j = i + 1;
  while (j < src.length) {
    const ch = src[j];
    if (ch === '\\') { text += ' '; j += 2; continue; }
    if (ch === quote) { j += 1; break; }
    if (ch === '\n') break; // unterminated; stop rather than run away
    text += ch;
    j += 1;
  }
  return { text, end: j };
}

/** Index just past the `}` matching the `{` at `open`. Skips strings. */
function matchBrace(src, open) {
  let depth = 0;
  let j = open;
  while (j < src.length) {
    const ch = src[j];
    if (ch === '"' || ch === "'") { j = readQuoted(src, j).end; continue; }
    if (ch === '`') { j = readTemplate(src, j).end; continue; }
    if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth === 0) return j + 1;
    }
    j += 1;
  }
  return j;
}

/**
 * A template literal starting at `i` (the backtick), flattened to class text.
 *
 * An interpolation contributes a `HOLE` plus whatever literals its own
 * expression holds, and the boundary either side is marked as fusing ONLY when
 * the adjacent static text has no whitespace there. That distinction is the whole
 * point: `` `text-${size}` `` must poison `text-`, while
 * `` `mt-1 text-micro ${x}` `` must keep both of its tokens.
 */
function readTemplate(src, i) {
  const pieces = [];
  let chunk = '';
  let j = i + 1;

  while (j < src.length) {
    const ch = src[j];
    if (ch === '\\') { chunk += ' '; j += 2; continue; }
    if (ch === '`') { j += 1; break; }
    if (ch === '$' && src[j + 1] === '{') {
      const end = matchBrace(src, j + 1);
      pieces.push({ kind: 'text', value: chunk });
      pieces.push({ kind: 'interp', value: src.slice(j + 2, Math.max(j + 2, end - 1)) });
      chunk = '';
      j = end;
      continue;
    }
    chunk += ch;
    j += 1;
  }
  pieces.push({ kind: 'text', value: chunk });

  let text = '';
  pieces.forEach((piece, k) => {
    if (piece.kind === 'text') {
      text += piece.value;
      return;
    }
    const before = pieces[k - 1];
    const after = pieces[k + 1];
    const leftFuses = before && before.kind === 'text' && before.value !== ''
      && !/\s$/.test(before.value);
    const rightFuses = after && after.kind === 'text' && after.value !== ''
      && !/^\s/.test(after.value);
    if (leftFuses) text += HOLE;
    text += ` ${flattenClassExpression(piece.value)} `;
    if (rightFuses) text += HOLE;
  });

  return { text, end: j };
}

/** An arrow: `(f) => 'flex'` really does return class text. Checked first. */
const ARROW_BEFORE = /=>\s*$/;

/**
 * `===`, `!==`, `==`, `!=`, `<=`, `>=`, `<`, `>`. A string on either side of one
 * of these is data, not a class.
 */
const COMPARISON_BEFORE = /(?:[=!<>]=|[<>])\s*$/;
const COMPARISON_AFTER = /^\s*(?:[=!]==?|\.|\?\.)/;

/**
 * The positions in which a string literal is a class list: a ternary branch, an
 * operand of `+`/`&&`/`||`/`??`, an object value, an arrow body, or the whole
 * expression. Notably absent are `(`, `,` and `[`, which is what keeps a call
 * argument, an array element or a property key out.
 *
 * This looks at the *immediately* preceding token only, which is why it is not
 * the whole story. `SIZE_CLASS[known ? size : 'sm']` puts `'sm'` one character
 * after a `:` and several tokens after a `[` — the branch of a ternary that is
 * itself a subscript. Reading backwards one token says "class text"; reading the
 * enclosing brackets says "index". `flattenClassExpression` tracks `[`-depth for
 * exactly that reason and settles it before asking this question at all.
 */
const CLASS_TEXT_BEFORE = /(?:\?|:|\+|&&|\|\||\?\?|=|\{|^)\s*$/;

/**
 * Whether a string literal preceded by `before` and followed by `after` is class
 * text rather than data.
 *
 * This distinction is not a nicety. `className` expressions in this codebase are
 * full of strings that are emphatically not classes — `userOS === 'windows'`,
 * `controlProps['aria-invalid']`, `bot.pnl.startsWith('+')`,
 * `notice.type === 'error'` — and a reader that harvested every literal reported
 * twelve of them as dead classes on its first run. Twelve false failures is how a
 * guard gets switched off.
 */
function isClassTextPosition(before, after) {
  if (ARROW_BEFORE.test(before)) return true;
  if (COMPARISON_BEFORE.test(before)) return false;
  if (COMPARISON_AFTER.test(after)) return false;
  return CLASS_TEXT_BEFORE.test(before);
}

/**
 * A JavaScript expression flattened to the class text it can contribute.
 *
 * String and template literals in a class-text position contribute verbatim; one
 * in a data position is folded back into the surrounding code. A run of code
 * contributes a `HOLE`, fused against its neighbour only across a `+` — because
 * `+` is concatenation and can produce a fragment, while `?`/`:`/`&&` produce
 * whole alternatives. Two literals joined by nothing but `+` are simply
 * concatenated, which is what `AssetSelector.jsx`'s three-line `INPUT_CLASS`
 * needs in order to keep `placeholder:text-text-muted`.
 *
 * INSIDE `[` … `]` NOTHING IS CLASS TEXT. `[`-depth is tracked as the expression
 * is scanned and any literal at depth above zero is folded into the code, whatever
 * token happens to precede it. `isClassTextPosition` sees one token back, and one
 * token back from `'sm'` in `SIZE_CLASS[known ? size : 'sm']` is a `:` — a ternary
 * branch, which is a class-text position everywhere else and is emphatically not
 * one here. `'sm'` is a key into a map whose *values* are the class strings
 * (`'gap-1 px-1.5 py-0.5 text-micro'`), and this guard reported it as a dead class
 * called `sm` for as long as depth went untracked. `variants['primary']` and
 * `map[cond ? 'a' : 'b']` are the same shape. A subscript is a lookup; the classes
 * are at the other end of it, in the object literal, which this reader either sees
 * there or does not see at all.
 *
 * NO EXEMPTION FOR ARRAY LITERALS, and that is a measurement rather than a
 * shrug. `['a', 'b'].join(' ')` is a genuine class-list idiom, and the usual way
 * to tell that `[` from a subscript's is to look at what precedes it — an
 * identifier, `]` or `)` means member access, anything else means a literal. It
 * is not applied here for two reasons. First, it would buy nothing: an element in
 * that array sits directly after `[` or `,`, and neither is in
 * `CLASS_TEXT_BEFORE`, so `['a', 'b'].join(' ')` already contributed no tokens
 * before this change and still contributes none. The only form the depth rule
 * newly hides is a *branch* inside an array literal —
 * `[cond ? 'a' : 'b'].join(' ')`, `[base, cond && 'p-2'].filter(Boolean)` — and
 * `src/` contains no such class list (every `].join(` in `src/` builds CSV rows,
 * a selector list or prose). Second, the cost is not symmetric: the heuristic's
 * failure mode is calling a subscript a literal — `SIZE_CLASS?.[known ? size :
 * 'sm']`, a `[` after a comment or a line break — and each of those hands back
 * the exact false failure this rule exists to remove. Losing harvest is the
 * direction this guard is allowed to be wrong in; see the header.
 */
function flattenClassExpression(expr) {
  const pieces = [];
  let code = '';
  let i = 0;
  // `[`-depth. Brackets are counted only where they appear as *code*: one inside
  // a string is consumed whole by `readQuoted`/`readTemplate` and never reaches
  // the counter, so `x === '[' ? 'p-2' : 'p-4'` still reads as class text and
  // `"text-[10px] min-w-[220px]"` is untouched. Clamped at zero so a stray `]` —
  // an unbalanced bracket in a regex, a span this reader mis-sliced — cannot bank
  // negative depth and then let a later real `[` look like depth zero.
  let subscript = 0;

  const pushCode = () => {
    if (code.trim()) pieces.push({ kind: 'code', value: code });
    code = '';
  };

  while (i < expr.length) {
    const ch = expr[i];
    if (ch === '"' || ch === "'") {
      const { text, end } = readQuoted(expr, i);
      if (subscript > 0 || !isClassTextPosition(code, expr.slice(end, end + 6))) {
        code += expr.slice(i, end); // data: keep it as code, contribute no tokens
        i = end;
        continue;
      }
      pushCode();
      pieces.push({ kind: 'text', value: text });
      i = end;
      continue;
    }
    if (ch === '`') {
      const { text, end } = readTemplate(expr, i);
      if (subscript > 0) {
        code += expr.slice(i, end); // `map[`${a}-${b}`]` is a key too
        i = end;
        continue;
      }
      pushCode();
      pieces.push({ kind: 'text', value: text });
      i = end;
      continue;
    }
    if (ch === '[') subscript += 1;
    else if (ch === ']') subscript = Math.max(0, subscript - 1);
    code += ch;
    i += 1;
  }
  pushCode();

  let text = '';
  for (const piece of pieces) {
    if (piece.kind === 'text') {
      text += piece.value;
      continue;
    }
    const trimmed = piece.value.trim();
    if (trimmed === '+') continue; // literal concatenation: join the two verbatim
    text += trimmed.startsWith('+') ? '' : ' ';
    text += HOLE;
    text += trimmed.endsWith('+') ? '' : ' ';
  }

  return text;
}

/** `className=` — the JSX attribute and the prop. */
const CLASS_NAME_SITE = /(?<![\w$])className\s*=\s*/g;

/**
 * `const badgeClass =`, `const INPUT_CLASS =`, `const controlClassName =`.
 * The suffix is what makes the site self-identifying; see the docblock.
 */
const CLASS_DECLARATION_SITE =
  /(?<![\w$])(?:const|let|var)\s+[\w$]*(?:[Cc]lass|CLASS)(?:e?s|Name|NAME|Names)?\s*=\s*/g;

/** Beyond this, an unterminated declaration is abandoned rather than guessed at. */
const DECLARATION_SPAN_LIMIT = 4000;

/** The expression that follows a declaration's `=`, up to its `;`. */
function readDeclarationExpression(src, from) {
  let depth = 0;
  let j = from;
  const limit = Math.min(src.length, from + DECLARATION_SPAN_LIMIT);
  while (j < limit) {
    const ch = src[j];
    if (ch === '"' || ch === "'") { j = readQuoted(src, j).end; continue; }
    if (ch === '`') { j = readTemplate(src, j).end; continue; }
    if ('([{'.includes(ch)) depth += 1;
    else if (')]}'.includes(ch)) depth -= 1;
    else if (ch === ';' && depth <= 0) return src.slice(from, j);
    j += 1;
  }
  return null; // no statement end in range: read nothing rather than over-read
}

/**
 * The class tokens a source file asks for.
 *
 * Comments are stripped first (see `source-scan.js`), so a docblock naming a
 * retired class — this suite's own header names nine of them — is not a call
 * site.
 */
export function extractClassTokens(source) {
  const code = stripComments(source);
  const flattened = [];

  for (const match of code.matchAll(CLASS_NAME_SITE)) {
    let at = match.index + match[0].length;
    while (at < code.length && /\s/.test(code[at])) at += 1;
    const ch = code[at];
    if (ch === '"' || ch === "'") {
      flattened.push(readQuoted(code, at).text);
    } else if (ch === '{') {
      const end = matchBrace(code, at);
      flattened.push(flattenClassExpression(code.slice(at + 1, Math.max(at + 1, end - 1))));
    }
    // Anything else (a spread, a JSX expression this reader does not model) is
    // left alone: an unread site contributes nothing and fails nothing.
  }

  for (const match of code.matchAll(CLASS_DECLARATION_SITE)) {
    const expr = readDeclarationExpression(code, match.index + match[0].length);
    if (expr !== null) flattened.push(flattenClassExpression(expr));
  }

  const tokens = new Set();
  const dropped = new Set();
  for (const text of flattened) {
    for (const token of text.split(/\s+/)) {
      if (!token) continue;
      if (token.includes(HOLE)) dropped.add(token.replaceAll(HOLE, '…'));
      else tokens.add(token);
    }
  }
  return { tokens, dropped };
}

// ---------------------------------------------------------------------------
// Scanning
// ---------------------------------------------------------------------------

const SCANNED_EXTENSIONS = Object.freeze(['.js', '.jsx', '.ts', '.tsx']);

/** `[{ relative, tokens, dropped }]` for every source file in `src/`. */
function scan() {
  const files = [];
  for (const full of collect(SRC, SCANNED_EXTENSIONS)) {
    const relative = relToSrc(full);
    if (isTestFile(relative)) continue;
    files.push({ relative, ...extractClassTokens(readFileSync(full, 'utf8')) });
  }
  return files.sort((a, b) => a.relative.localeCompare(b.relative));
}

const SCANNED = scan();

/** `token -> the files that ask for it`. */
const USAGE = new Map();
for (const file of SCANNED) {
  for (const token of file.tokens) {
    if (!USAGE.has(token)) USAGE.set(token, []);
    USAGE.get(token).push(file.relative);
  }
}

/** The twelve hand-written classes in `index.css`. See the docblock. */
const THE_TWELVE_UTILITIES = Object.freeze([
  'section-container',
  'section-inner',
  'glass-nav',
  'btn-primary',
  'btn-ghost',
  'btn-gold',
  'card-surface',
  'card-elevated',
  'text-gradient-cyan',
  'terminal-frame',
  'scrollbar-hide',
  'badge-status',
]);

/**
 * Classes that `src/` asks for today and the build does not produce.
 *
 * THESE ARE REAL DEFECTS, NOT EXEMPTIONS. Each one is quarantined here with what
 * is wrong and who clears it. The list is pinned by value and asserted non-stale
 * below, so it cannot grow quietly and cannot outlive the fix.
 *
 * EIGHT OF THE ORIGINAL NINE ARE FIXED. When this guard landed with task 3.4 it
 * carried nine entries; the two source defects behind eight of them were repaired
 * directly and their entries removed. What was wrong is kept below, because the
 * value of a finding is not only in the fix:
 *
 * 1. FIXED — `pages/StrategyMarketplace.jsx` carried seven arbitrary-value classes
 *    with the opacity slash missing: `bg-[#00D4FF]5` where `bg-[#00D4FF]/5` was
 *    meant, `hover:bg-[#26A69A]90` where `.../90` was, and five more, over eight
 *    occurrences (`bg-[#EF5350]20` appeared twice). `components/ui/Badge.jsx`
 *    writes the same values correctly (`bg-[#00D4FF]/10`), which is what
 *    identified these as typos rather than a house convention. Effect while they
 *    stood: the marketplace hero glow, both notice banners' background wash, and
 *    the subscribe and clone buttons' hover state all rendered nothing —
 *    precisely §1.2's failure mode, in a file design.md's §1.1 table calls
 *    "already fully Tailwind". Each was repaired by inserting the missing `/` and
 *    nothing else; the hex literals stay, so the `no-colour-literals` budget for
 *    the file is unchanged at 188 and task 22.1's marketplace retoken still owns
 *    replacing them with tokens.
 * 2. FIXED — `components/builder/AssetSelector.jsx` used `bg-bg-base` on the asset
 *    dropdown's active row. There is no `--color-bg-base`; the compatibility
 *    aliases in `tokens.css` are `bg-primary`, `bg-surface`, `bg-elevated` and
 *    `bg-3`. Effect while it stood: the active row had no highlight, so keyboard
 *    navigation through that listbox was invisible — an accessibility defect, not
 *    only a cosmetic one. Repaired as `bg-bg-3`, NOT `bg-bg-elevated` as this
 *    docblock first proposed: the listbox `<ul>` is itself `bg-bg-elevated`
 *    (`--color-surface-raised`, #151821), so the elevated alias would have
 *    painted the active row the colour of the list it sits in and left the
 *    keyboard position just as invisible under a name that resolves.
 *    `--color-surface-inset` (#1A202C) is the only surface alias lighter than the
 *    listbox. Recorded for the design pass: at #1A202C on #151821 that highlight
 *    is about 1.09:1, well under the 3:1 WCAG 1.4.11 asks of a non-text
 *    indicator. It is the best any of the four aliases does on this ramp — the
 *    other three are darker than the listbox — so raising it means adding a
 *    highlight token rather than choosing a different alias.
 * 3. `components/landing/FinalCTA.jsx` — `animate-pulse-glow`. Deliberately dead
 *    and deliberately still here: `tokens.css` records that `--animate-pulse-glow`
 *    was NOT carried over because it is a coloured glow and Requirement 1.5
 *    retires those. The animation is gone as intended; the class name was left on
 *    the element. Cleared by deleting the token from that `className` — the
 *    landing page is out of scope for redesign (§17.2), so it waits for a
 *    landing-page pass.
 */
const KNOWN_DEAD_CLASSES = Object.freeze({
  'animate-pulse-glow': 'components/landing/FinalCTA.jsx — retired by Requirement 1.5',
});

/** Four classes that must be in any honest harvest of the current build. */
const KNOWN_LIVE = Object.freeze(['font-mono', 'text-micro', 'flex', 'section-container']);

/** The §1.2 classes. Removed by task 3.1, so absent from the build. */
const KNOWN_DEAD = Object.freeze([
  'text-caption',
  'text-caption-sm',
  'text-body-sm',
  'text-body-lg',
  'text-heading',
  'text-heading-lg',
  'shadow-glow',
  'shadow-glow-green',
]);

const describeToken = (token) => {
  const files = USAGE.get(token) ?? [];
  const shown = files.slice(0, 3).join(', ');
  return `${token} — ${shown}${files.length > 3 ? ` (+${files.length - 3} more)` : ''}`;
};

// ---------------------------------------------------------------------------
// 1. The build gate
// ---------------------------------------------------------------------------

describe('dead-tailwind: the build gate', () => {
  // This one test always runs, whether or not there is a build. Everything that
  // needs the stylesheet goes through `needsBuild`, which skips carrying
  // SKIP_NOTE — so the run says which tests did not happen and why, rather than
  // reporting a number of skips nobody can account for. This test is where the
  // long form gets printed.
  //
  // The warning is emitted from inside the test body, not at module load:
  // Vitest attaches console output to the running task, and a `console.warn`
  // during import is swallowed — which would have made the notice invisible
  // exactly when it is needed. Verified both ways.
  it('checks a real stylesheet, or says in words why it did not', () => {
    if (HAS_BUILD) {
      expect(STYLESHEETS.length).toBeGreaterThan(0);
      expect(RESOLVED.size).toBeGreaterThan(500);
      return;
    }
    console.warn(SKIP_NOTICE);
    // Not a failure — but assert the notice is intact, so the skip can never
    // degrade into a silent pass with nothing printed.
    expect(SKIP_NOTICE).toContain('npm run build');
    expect(SKIP_NOTICE).toContain('SKIPPED');
  });
});

// ---------------------------------------------------------------------------
// 2. The extraction method itself
// ---------------------------------------------------------------------------

describe('dead-tailwind: the extraction method', () => {
  const tokensOf = (source) => [...extractClassTokens(source).tokens].sort();
  const droppedOf = (source) => [...extractClassTokens(source).dropped].sort();

  it('reads a plain className attribute', () => {
    expect(tokensOf('<div className="flex items-center gap-2" />')).toEqual([
      'flex', 'gap-2', 'items-center',
    ]);
    expect(tokensOf("<div className='w-5 h-5' />")).toEqual(['h-5', 'w-5']);
    expect(tokensOf('<div className = "p-6" />')).toEqual(['p-6']);
  });

  it('reads a template literal, static parts and conditional branches alike', () => {
    expect(
      tokensOf("<p className={`mt-1 text-micro ${bad ? 'text-accent-loss' : 'text-text-muted'}`} />"),
    ).toEqual(['mt-1', 'text-accent-loss', 'text-micro', 'text-text-muted']);
  });

  it('reads a bare conditional expression', () => {
    expect(tokensOf("<p className={open ? 'block p-1.5' : 'hidden'} />")).toEqual([
      'block', 'hidden', 'p-1.5',
    ]);
  });

  it('joins two string literals concatenated with +', () => {
    // AssetSelector.jsx's INPUT_CLASS is three literals joined this way, and the
    // first token after each `+` is a real class, not a fragment.
    expect(tokensOf("const A_CLASS = 'w-full rounded ' + 'px-2 py-1';")).toEqual([
      'px-2', 'py-1', 'rounded', 'w-full',
    ]);
    // Split mid-token, `+` still concatenates — and must not produce two halves.
    expect(tokensOf("const B_CLASS = 'text-' + 'micro';")).toEqual(['text-micro']);
  });

  it('reads a class-list declaration by its name', () => {
    expect(tokensOf("const SELECT_CLASS = 'w-full font-mono text-body';")).toEqual([
      'font-mono', 'text-body', 'w-full',
    ]);
    expect(tokensOf("const combinedClass = `rounded-xl p-6`;")).toEqual(['p-6', 'rounded-xl']);
    expect(tokensOf("const controlClassName = (f) => `${BASE} border-border-default`;")).toEqual([
      'border-border-default',
    ]);
  });

  it('drops a fragment rather than reporting it as a dead class', () => {
    // The one thing this must never do: invent `text-` and fail the build on it.
    expect(tokensOf('<p className={`text-${size}`} />')).toEqual([]);
    expect(droppedOf('<p className={`text-${size}`} />')).toContain('text-…');
    expect(tokensOf('<p className={`${prefix}-lg`} />')).toEqual([]);
    expect(tokensOf("const cls = 'bg-' + tone;")).toEqual([]);
  });

  it('keeps whole tokens when the interpolation is whitespace-delimited', () => {
    // The common case, and the one a blunt "anything near a ${} is suspect" rule
    // would throw away.
    expect(tokensOf('<p className={`flex ${gap} p-6`} />')).toEqual(['flex', 'p-6']);
    expect(tokensOf('<p className={`${base} p-6`} />')).toEqual(['p-6']);
    expect(tokensOf('<p className={`p-6 ${base}`} />')).toEqual(['p-6']);
  });

  it('ignores strings that are data rather than classes', () => {
    // Every one of these is a real form from src/, and every one of them was
    // reported as a dead class by the first version of this reader.
    expect(tokensOf("<div className={`p-4 ${userOS === 'windows' ? 'border-x' : ''}`} />")).toEqual([
      'border-x', 'p-4',
    ]);
    expect(tokensOf("<div className={`${notice.type === 'error' ? 'bg-x' : 'bg-y'}`} />")).toEqual([
      'bg-x', 'bg-y',
    ]);
    expect(
      tokensOf("<i className={SELECT_CLASS + (props['aria-invalid'] ? ' border-x' : ' border-y')} />"),
    ).toEqual(['border-x', 'border-y']);
    expect(tokensOf("<i className={`${p.startsWith('+') ? 'text-x' : 'text-y'}`} />")).toEqual([
      'text-x', 'text-y',
    ]);
    expect(tokensOf("const aClass = fmt('flex', x);")).toEqual([]);
  });

  it('still reads a class list out of an object value or an arrow body', () => {
    expect(tokensOf("const variantClasses = { cyan: 'bg-x border-y' };")).toEqual([
      'bg-x', 'border-y',
    ]);
    expect(tokensOf("const rowClass = (r) => 'text-right';")).toEqual(['text-right']);
  });

  it('reads a subscript as a lookup key, not as class text', () => {
    // THE REGRESSION THIS PINS. `ds/StatusBadge.jsx` renders
    //   `… tracking-wide ${SIZE_CLASS[known ? size : 'sm']} ${className}`.trim()
    // and this guard reported a dead class called `sm`. It is a key into a map
    // whose values are the class lists (`'gap-1 px-1.5 py-0.5 text-micro'`), so
    // the class names are at the other end of the lookup and `sm` is not one of
    // them. One token back from `'sm'` is a `:`, which is a class-text position
    // everywhere else — only the enclosing `[` says otherwise.
    expect(
      tokensOf("<span className={`rounded-sm border ${SIZE_CLASS[known ? size : 'sm']} ${className}`.trim()} />"),
    ).toEqual(['border', 'rounded-sm']);
    expect(tokensOf("<div className={SIZE_CLASS[known ? size : 'sm']} />")).toEqual([]);
    expect(tokensOf("<div className={variants['primary']} />")).toEqual([]);
    expect(tokensOf("<div className={map[cond ? 'a' : 'b']} />")).toEqual([]);
    // Seen and dropped, not silently skipped: the site still registers as
    // unknowable, which is what keeps `text-${size}` honest as well.
    expect(droppedOf("<div className={map[cond ? 'a' : 'b']} />")).toContain('…');
  });

  it('closes the subscript again, and does not open one inside a string', () => {
    // `[`-depth, not "there is a `[` somewhere". A lookup that is only the
    // *condition* leaves the branches readable.
    expect(tokensOf("const aClass = obj[key] ? 'p-2' : 'p-4';")).toEqual(['p-2', 'p-4']);
    expect(tokensOf("<div className={props['aria-invalid'] ? 'border-x' : 'border-y'} />")).toEqual([
      'border-x', 'border-y',
    ]);
    // A bracket inside a literal is text, and must not count as depth — otherwise
    // one `'['` would silence the rest of the file, and arbitrary-value classes
    // are made of brackets.
    expect(tokensOf("<div className={x === '[' ? 'p-2' : 'p-4'} />")).toEqual(['p-2', 'p-4']);
    expect(tokensOf("<div className={`text-[10px] ${c ? 'min-w-[220px]' : 'w-full'}`} />")).toEqual([
      'min-w-[220px]', 'text-[10px]', 'w-full',
    ]);
  });

  it('still reads every class-text position the subscript rule must not swallow', () => {
    // The four forms the fix had to leave alone, asserted together so a widening
    // of the bracket rule fails here rather than going quiet in `src/`.
    expect(tokensOf("<div className={cond ? 'a' : 'b'} />")).toEqual(['a', 'b']);
    expect(tokensOf("const variantClasses = { cyan: 'bg-x border-y' };")).toEqual([
      'bg-x', 'border-y',
    ]);
    expect(tokensOf("<div className={`flex ${cond ? 'p-2' : 'p-4'}`} />")).toEqual([
      'flex', 'p-2', 'p-4',
    ]);
    expect(tokensOf("const aClass = 'a ' + 'b';")).toEqual(['a', 'b']);
  });

  it('reads nothing out of an array literal, which is a loss and not a defect', () => {
    // `['a', 'b'].join(' ')` is a real class-list idiom and this reader never
    // read it: an element follows `[` or `,`, and neither is a class-text
    // position, so both of these returned nothing before `[`-depth existed too.
    expect(tokensOf("const aClass = ['flex', 'p-2'].join(' ');")).toEqual([]);
    // What the depth rule newly hides is a branch inside the array. Pinned as a
    // known blind spot rather than left to be discovered: no class list in `src/`
    // is written this way, and telling this `[` from a subscript's by the token
    // before it risks handing back the `sm` false failure. See
    // `flattenClassExpression`.
    expect(tokensOf("const aClass = [cond ? 'a' : 'b'].join(' ');")).toEqual([]);
    expect(tokensOf("const aClass = [base, cond && 'p-2'].filter(Boolean).join(' ');")).toEqual([]);
  });

  it('sees nothing in a fully computed className, and says nothing about it', () => {
    expect(tokensOf('<div className={combined} {...props} />')).toEqual([]);
    expect(tokensOf('<div className={variants[key]} />')).toEqual([]);
    expect(tokensOf('function Icon({ className }) { return <svg className={className} />; }')).toEqual([]);
  });

  it('ignores a class named only in a comment', () => {
    // Nine retired class names appear in this file's own header.
    expect(tokensOf('// className="text-caption-sm" was dead')).toEqual([]);
    expect(tokensOf('/* <div className="shadow-glow" /> */')).toEqual([]);
  });

  it('is not fooled by a lookalike attribute name', () => {
    expect(tokensOf('<div dotClassName="x-1" />')).toEqual([]);
    expect(tokensOf('const classified = classifyAssetError(e);')).toEqual([]);
  });

  it('keeps variant, arbitrary-value and opacity forms intact', () => {
    expect(
      tokensOf('<div className="hover:bg-x focus:ring-2 md:flex text-[10px] border-accent-cyan/30 -translate-y-0.5" />'),
    ).toEqual([
      '-translate-y-0.5',
      'border-accent-cyan/30',
      'focus:ring-2',
      'hover:bg-x',
      'md:flex',
      'text-[10px]',
    ]);
  });
});

// ---------------------------------------------------------------------------
// 3. The harvest itself
// ---------------------------------------------------------------------------

describe('dead-tailwind: the harvest', () => {
  it('reads class names out of selectors, not out of declaration values', () => {
    const names = harvestClassNames('.a{padding:.75rem;margin:.5rem}.b-2{color:red}');
    expect([...names].sort()).toEqual(['a', 'b-2']);
  });

  it('undoes the escaping Tailwind v4 applies to awkward names', () => {
    const css = '.hover\\:text-x:hover{color:red}.min-w-\\[220px\\]{min-width:220px}'
      + '.w-1\\.5{width:1px}.border-accent-cyan\\/30{border-color:red}.\\!flex{display:flex}';
    expect([...harvestClassNames(css)].sort()).toEqual([
      '!flex', 'border-accent-cyan/30', 'hover:text-x', 'min-w-[220px]', 'w-1.5',
    ]);
  });

  it('skips at-rule preludes and reads the rules nested inside them', () => {
    const css = '@media(min-width:640px){.section-container{padding-left:1.5rem}}';
    expect([...harvestClassNames(css)]).toEqual(['section-container']);
    expect([...harvestClassNames('@layer properties{*,:before{--tw-x:0}}')]).toEqual([]);
  });

  it('is not derailed by a brace inside a CSS string', () => {
    expect([...harvestClassNames('.a:after{content:"{"}.b{color:red}')].sort()).toEqual(['a', 'b']);
  });
});

// ---------------------------------------------------------------------------
// 4. Scope — and non-vacuity
// ---------------------------------------------------------------------------

describe('dead-tailwind: scope', () => {
  it('scans all of src/ and finds class usage across it', () => {
    expect(SCANNED.length).toBeGreaterThan(100);
    expect(SCANNED.filter((f) => f.tokens.size > 0).length).toBeGreaterThan(30);
    expect(USAGE.size).toBeGreaterThan(300);
  });

  it('leaves test files out', () => {
    expect(SCANNED.some((f) => isTestFile(f.relative))).toBe(false);
  });

  it('finds the classes the pages visibly use, so a broken scan cannot look green', () => {
    // Non-vacuity for the extraction half. If `className` stopped matching, or
    // path resolution broke the way source-scan.js documents, USAGE would be
    // empty and every assertion below would pass over nothing.
    for (const token of ['flex', 'font-mono', 'text-micro', 'section-container']) {
      expect(USAGE.has(token), `no file asks for \`${token}\``).toBe(true);
    }
    expect(USAGE.get('text-micro').length).toBeGreaterThan(3);
  });
});

describe('dead-tailwind: THE_HARVEST_IS_HONEST', () => {
  needsBuild('finds the classes the build certainly contains', () => {
    for (const token of KNOWN_LIVE) {
      expect(RESOLVED.has(token), `\`${token}\` is missing from the built CSS`).toBe(true);
    }
  });

  needsBuild('does not find the §1.2 classes, which is what makes a failure meaningful', () => {
    // If the harvester over-matched — counting declaration values, or treating
    // every dot as a class — these would appear and this guard would resolve
    // anything at all. They are the control group.
    const phantom = KNOWN_DEAD.filter((token) => RESOLVED.has(token));
    expect(
      phantom,
      `The harvest found classes that tailwind.config.js declared and Tailwind v4 never\n`
        + `compiled (design.md §1.2). Either the build is stale, or the harvester is\n`
        + `matching things that are not selectors — in which case this whole guard is\n`
        + `vacuous:\n${list(phantom)}`,
    ).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 5. The rule
// ---------------------------------------------------------------------------

describe('dead-tailwind: every class src/ asks for exists', () => {
  needsBuild('resolves every extracted className token in the built stylesheet', () => {
    const dead = [...USAGE.keys()]
      .filter((token) => !RESOLVED.has(token) && !(token in KNOWN_DEAD_CLASSES))
      .sort();

    expect(
      dead,
      `These class names are used in src/ and compile to nothing.\n\n`
        + `An element carrying one of them renders as if the attribute were absent — no\n`
        + `error, no warning, exactly the failure design.md §1.2 describes, where 143\n`
        + `elements silently inherited their font size for months.\n\n`
        + `Check for: a typo; a missing \`/\` before an opacity suffix; a class from the\n`
        + `deleted tailwind.config.js; a token name that is not in src/styles/tokens.css;\n`
        + `or a stale build (re-run npm run build).\n${list(dead.map(describeToken))}`,
    ).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 5b. The quarantine is exact and temporary
// ---------------------------------------------------------------------------

describe('dead-tailwind: the known dead classes', () => {
  it('is exactly the one deliberate case left, and cannot grow unnoticed', () => {
    // Widening this is the easy way to make the guard stop complaining, so it is
    // pinned by value. Adding one means editing this assertion, which means
    // saying so in the diff.
    //
    // This landed with nine. Eight were source defects and have been fixed — the
    // seven marketplace opacity-slash typos and `AssetSelector.jsx`'s
    // `bg-bg-base` — leaving only the class that is dead on purpose. See the
    // FINDING block above for what each of the eight was.
    expect(Object.keys(KNOWN_DEAD_CLASSES).sort()).toEqual(['animate-pulse-glow']);
  });

  it('names only classes src/ still asks for', () => {
    // A stale entry is a hole in the guard. When the marketplace retoken or the
    // builder pass removes one of these, this fails until the entry goes too.
    const stale = Object.keys(KNOWN_DEAD_CLASSES)
      .filter((token) => !USAGE.has(token))
      .map((token) => `${token} — no longer used in src/`);

    expect(
      stale,
      `These entries are no longer needed. The class they excuse has been removed from\n`
        + `src/, so delete the entry from KNOWN_DEAD_CLASSES in this file — an exception\n`
        + `nobody needs is a hole nobody is watching:\n${list(stale)}`,
    ).toEqual([]);
  });

  needsBuild('names only classes that really are still dead', () => {
    // The other direction: if a fix lands (or a token gains a definition), the
    // class starts resolving and the entry must go, or it hides a future
    // regression of the same name.
    const revived = Object.keys(KNOWN_DEAD_CLASSES)
      .filter((token) => RESOLVED.has(token))
      .map((token) => `${token} — now resolves; remove the entry`);

    expect(revived, `${list(revived)}`).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 6. index.css's twelve hand-written classes
// ---------------------------------------------------------------------------

describe('dead-tailwind: THE_TWELVE_UTILITIES', () => {
  it('is still exactly the twelve blocks index.css declares', () => {
    // Comments stripped, or index.css's own header line "the landing-page
    // @utility compositions" registers as a thirteenth block called
    // `compositions`. `lineComments: false` because `//` is not a comment in CSS.
    const declared = [
      ...stripComments(readFileSync(path.join(SRC, 'index.css'), 'utf8'), {
        lineComments: false,
      }).matchAll(/@utility\s+([A-Za-z0-9_-]+)/g),
    ].map((m) => m[1]);

    expect([...declared].sort()).toEqual([...THE_TWELVE_UTILITIES].sort());
  });

  needsBuild('emits every one that src/ actually uses', () => {
    // `@utility` is on-demand, so these need no allowlist: a used one is
    // compiled and resolves like any Tailwind class. This asserts that instead
    // of assuming it.
    const used = THE_TWELVE_UTILITIES.filter((name) => USAGE.has(name));
    const missing = used.filter((name) => !RESOLVED.has(name));

    expect(used.length).toBeGreaterThanOrEqual(7);
    expect(
      missing,
      `These index.css @utility classes have call sites in src/ but produce no CSS:\n`
        + `${list(missing.map(describeToken))}`,
    ).toEqual([]);
  });

  needsBuild('records which of the twelve have no call site at all', () => {
    // Not a failure — an unused @utility correctly compiles to nothing, and that
    // is not the §1.2 defect this guard exists for. Pinned so the set cannot
    // drift unnoticed, and reported in the docblock: `scrollbar-hide` and
    // `badge-status` are dead definitions awaiting a call site or deletion, while
    // `glass-nav`, `btn-gold` and `card-elevated` are in the shipped bundle only
    // because a test file's comment names them.
    const unused = THE_TWELVE_UTILITIES.filter((name) => !USAGE.has(name));
    expect(unused.sort()).toEqual(
      ['badge-status', 'btn-gold', 'card-elevated', 'glass-nav', 'scrollbar-hide'].sort(),
    );
    expect(RESOLVED.has('scrollbar-hide')).toBe(false);
    expect(RESOLVED.has('badge-status')).toBe(false);
  });
});
