/**
 * Shared source-scanning primitives for the M1 CI guards (vyomquant-ui-redesign
 * tasks 1.9-1.12). Not a `*.test.js`, so vitest does not collect it.
 *
 * Four guards read the same tree and ask different questions of it. The parts
 * that are easy to get subtly wrong — where `src/` is, which files are source,
 * and where the comments are — live here once, so a lesson learned in one guard
 * is not re-learned in the next three.
 *
 * ===========================================================================
 * PATH RESOLUTION — read before replacing this with `import.meta.url`
 * ===========================================================================
 * `SRC` is resolved from `__dirname`. It must NOT be written as
 * `new URL('../../../src/…', import.meta.url)`.
 *
 * That second form does not mean here what it means in plain Node. Vite's
 * `asset-import-meta-url` plugin pattern-matches the literal
 * `new URL(<specifier>, import.meta.url)` expression during transform and
 * rewrites it into an *asset* lookup, which yields a server-root-relative path
 * (`/src/pages/Dashboard.jsx`). Joined back onto a `file:` base that resolves to
 * the drive root — `C:\src\pages\…` — so every read raises ENOENT at collection
 * time and the file reports "0 tests" instead of failing. A green-looking
 * non-run, which is the worst outcome a guard can have.
 *
 * `legacyTokenShim.test.js` hit exactly that and carries the same note.
 * `__dirname` is injected by the runner, is a real filesystem path, and no
 * transform touches it. It is what every other source-reading suite here
 * (`builder.architecture`, `strategyRename`, `nodeTrace`) already uses.
 */
import { readdirSync, statSync } from 'node:fs';
import path from 'node:path';

/** `<repo>/algo22-terminal`. This file lives at `tests/unit/guards/`. */
export const TERMINAL_ROOT = path.resolve(__dirname, '..', '..', '..');

/** `<repo>/algo22-terminal/src`. */
export const SRC = path.join(TERMINAL_ROOT, 'src');

/** Forward-slashed, so budget keys read the same on every platform. */
export const toPosix = (p) => p.split(path.sep).join('/');

/** A path relative to `SRC`, forward-slashed. */
export const relToSrc = (full) => toPosix(path.relative(SRC, full));

/**
 * Test files are outside every guard's scan.
 *
 * A test asserting `expect(...).toBe('#26A69A')`, or one that greps for
 * `const C =` in order to prove a rule, is doing its job. Guards must not
 * argue with tests.
 */
export const isTestFile = (relative) =>
  relative.split('/').includes('__tests__') || /\.(test|spec)\.[jt]sx?$/.test(relative);

/**
 * Every file under `dir` whose extension is in `extensions`, recursively.
 * Returns absolute paths, sorted for a stable report order.
 */
export function collect(dir, extensions, out = []) {
  for (const entry of readdirSync(dir).sort()) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) collect(full, extensions, out);
    else if (extensions.includes(path.extname(entry))) out.push(full);
  }
  return out;
}

/** Complete same-line `'…'` / `"…"` spans. Used only to locate `//`. */
const SAME_LINE_QUOTED = /'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"/g;

/**
 * Blank out comments, preserving length and line breaks so that reported line
 * numbers still line up with the file on disk.
 *
 * ---------------------------------------------------------------------------
 * WHY THIS IS NOT A TOKENISER
 * ---------------------------------------------------------------------------
 * Deliberately not a JavaScript tokeniser. A tokeniser has to decide whether
 * `/` opens a regex literal, and in JSX it cannot: `</div>` looks exactly like
 * the start of one. An earlier attempt at these guards did tokenise, mistook the
 * apostrophe in JSX text (`Don't`) for a string opener, swallowed the rest of
 * the file as a string, and lost 33 of `StrategyBuilder.jsx`'s 148 `C.`
 * references — a budget seeded from a number that was quietly 22% low.
 *
 * Comments are all that needs removing, and comments can be found without
 * answering the regex-vs-division question. Strings are deliberately left
 * intact: in this codebase the things the guards look for live inside strings
 * (`style={{ color: "#ef4444" }}`), so stripping strings would blind them.
 *
 * `//` inside a complete same-line quoted span, and `://` in a URL, are not
 * comment starts. Pass `lineComments: false` for `.css`, where `//` is not a
 * comment at all.
 */
export function stripComments(source, { lineComments = true } = {}) {
  // `source.split('')`, NOT `[...source]`. Every index used below — the `/*`
  // scan, `blank(i, j)`, and the line-comment pass's `offset + k` — is a UTF-16
  // offset taken from `source` or from `String.prototype.length`. The spread
  // operator iterates *code points*, so it collapses each surrogate pair into
  // one element and the two index spaces drift apart by one per pair.
  //
  // That is not theoretical. `pages/Strategies.jsx` holds 18 emoji, so with the
  // spread form every blank after the first one landed 1..18 characters to the
  // left of its comment: the comment survived (its contents got counted) and an
  // equal run of real code was blanked instead (its contents got missed). Both
  // directions of miscount, in the one place every guard here trusts blindly.
  // `split('')` splits by code unit, so the spaces land where they are aimed,
  // and `join('')` still reassembles the pairs intact.
  const chars = source.split('');
  const blank = (from, to) => {
    for (let k = from; k < to && k < chars.length; k += 1) {
      if (chars[k] !== '\n') chars[k] = ' ';
    }
  };

  for (let i = 0; i < source.length - 1; i += 1) {
    if (source[i] === '/' && source[i + 1] === '*') {
      let j = i + 2;
      while (j < source.length && !(source[j] === '*' && source[j + 1] === '/')) j += 1;
      j = Math.min(source.length, j + 2);
      blank(i, j);
      i = j - 1;
    }
  }

  if (!lineComments) return chars.join('');

  let offset = 0;
  for (const line of chars.join('').split('\n')) {
    const masked = line.replace(SAME_LINE_QUOTED, (m) => ' '.repeat(m.length));
    for (let k = 0; k < masked.length - 1; k += 1) {
      if (masked[k] === '/' && masked[k + 1] === '/' && masked[k - 1] !== ':') {
        blank(offset + k, offset + line.length);
        break;
      }
    }
    offset += line.length + 1;
  }

  return chars.join('');
}

/** Indent a list of report lines under a failure message. */
export const list = (rows) => rows.map((r) => `  ${r}`).join('\n');
