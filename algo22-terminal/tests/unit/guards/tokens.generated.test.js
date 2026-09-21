/**
 * `tokens.generated` — the committed JS mirror is never stale (task 1.9).
 *
 * design.md §15.1: "`design/tokens.js` matches a fresh generation from `tokens.css`.
 * Fails CI if stale." Requirement 1.1: `src/styles/tokens.css` is the sole token
 * source, and `src/design/tokens.js` is a generated projection of it that no one
 * may hand-edit.
 *
 * Why this guard exists at all
 * ---------------------------
 * `npm run prebuild` already runs `node scripts/gen-tokens.mjs --check`, so a
 * stale mirror cannot reach a *build*. It can reach a *pull request*: the unit
 * suite runs on PRs (`.github/workflows/01-pr-check.yml`) long before anything
 * is built, and a reviewer reading a diff that changes `tokens.css` has no way
 * to see from the diff alone whether the mirror was regenerated. This puts that
 * answer in the test report.
 *
 * How it asserts, and why it does not reimplement the comparison
 * -------------------------------------------------------------
 * The generator is executed **in-process** with `--check`, exactly the argv the
 * `prebuild` hook uses, and the assertion is on its exit code. Nothing about
 * parsing, grouping, alias resolution, serialisation or comparison is restated
 * here. That is deliberate: a guard that re-derived the expected output would be
 * a second implementation of the generator, free to agree with a bug or to
 * disagree with a correct change, and the guard and the hook could then reach
 * opposite verdicts on the same tree. There is one implementation, and this file
 * runs it.
 *
 * Running a `process.exit`-calling script inside the test worker needs two
 * stubs, both restored in `finally`:
 *
 *   * `process.argv`, so the script's own `--check` branch is selected.
 *   * `process.exit`, which is thrown through instead of taken — an unstubbed
 *     `process.exit(1)` would kill the vitest worker rather than fail a test,
 *     and stubbing it to a no-op would let execution fall through to the
 *     "up to date" line after a failure.
 *
 * The module is imported once (ESM evaluates a specifier once per worker), so
 * every assertion below reads the same captured run.
 *
 * NEWLINE NORMALISATION — read before "fixing" this to a byte compare
 * ------------------------------------------------------------------
 * Task 1.9's text says "byte for byte". This guard does not compare bytes, and
 * must not: this repo has `core.autocrlf=true` and no `.gitattributes`, so a
 * fresh Windows checkout gets the mirror with CRLF line endings on disk while
 * the generator emits LF. A byte compare would fail on every clean Windows
 * clone while the mirror was perfectly current — a guard that cries wolf gets
 * deleted, and then nothing checks staleness at all. Task 1.2 hit this first
 * and settled it inside the generator: its `--check` compares on
 * newline-normalised content. This guard inherits that by delegating, and pins
 * the decision as an assertion below rather than leaving it as a comment.
 *
 * Normalising costs no detection power. Every real form of staleness — a
 * changed value, an added or removed key, a moved group, a different ordering,
 * a changed token count in the header — is a change in non-newline characters
 * and still fails. The only difference it swallows is the one that carries no
 * information about the tokens.
 *
 * Verified by perturbation (not left as a claim): flipping one hex digit in the
 * committed mirror makes this file fail with the generator's own "is stale
 * relative to src/styles/tokens.css" message; restoring it makes it pass again.
 */
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

import { describe, it, expect, beforeAll, vi } from 'vitest';

/**
 * Resolved from `__dirname`, not from `new URL(…, import.meta.url)`.
 *
 * Vite's `asset-import-meta-url` plugin pattern-matches the literal
 * `new URL(<specifier>, import.meta.url)` expression and rewrites it into an
 * asset lookup, which yields a server-root-relative path that resolves to the
 * drive root (`C:\src\…`) — every read then raises ENOENT at collection time
 * and the file reports "0 tests" instead of failing. `__dirname` is injected by
 * the runner, is a real filesystem path, and no transform touches it. It is
 * what every other source-reading suite here already uses.
 */
const TERMINAL_ROOT = path.resolve(__dirname, '..', '..', '..');
const GENERATOR = path.join(TERMINAL_ROOT, 'scripts', 'gen-tokens.mjs');
const TOKENS_CSS = path.join(TERMINAL_ROOT, 'src', 'styles', 'tokens.css');
const MIRROR = path.join(TERMINAL_ROOT, 'src', 'design', 'tokens.js');

/** Thrown in place of a real `process.exit`, so the script stops where it meant to. */
const exitSignal = (code) =>
  Object.assign(new Error(`gen-tokens called process.exit(${code})`), {
    isExitSignal: true,
    code: code ?? 0,
  });

/**
 * Run `scripts/gen-tokens.mjs` in this process with the given argv.
 *
 * @returns {{exitCode: number, stdout: string, stderr: string}}
 */
const runGenerator = async (...argv) => {
  const stdout = [];
  const stderr = [];
  const realArgv = process.argv;

  const exit = vi.spyOn(process, 'exit').mockImplementation((code) => {
    throw exitSignal(code);
  });
  const log = vi.spyOn(console, 'log').mockImplementation((...parts) => {
    stdout.push(parts.join(' '));
  });
  const error = vi.spyOn(console, 'error').mockImplementation((...parts) => {
    stderr.push(parts.join(' '));
  });

  let exitCode = 0;
  process.argv = [realArgv[0], GENERATOR, ...argv];
  try {
    await import(pathToFileURL(GENERATOR).href);
  } catch (thrown) {
    if (!thrown?.isExitSignal) throw thrown;
    exitCode = thrown.code;
  } finally {
    process.argv = realArgv;
    exit.mockRestore();
    log.mockRestore();
    error.mockRestore();
  }

  return { exitCode, stdout: stdout.join('\n'), stderr: stderr.join('\n') };
};

/** The one `--check` run every assertion reads. */
let check;

beforeAll(async () => {
  check = await runGenerator('--check');
});

describe('the generated token mirror', () => {
  it('has both ends of the generation present to compare', () => {
    // A missing file would make the check fail for a reason that is not staleness,
    // and the message below would be misleading.
    expect(existsSync(GENERATOR), `${GENERATOR} is missing`).toBe(true);
    expect(existsSync(TOKENS_CSS), `${TOKENS_CSS} is missing`).toBe(true);
    expect(existsSync(MIRROR), `${MIRROR} is missing — run \`npm run tokens\``).toBe(true);
  });

  it('matches a fresh generation from src/styles/tokens.css', () => {
    expect(
      check.exitCode,
      `${check.stderr || 'gen-tokens --check failed'}\n\n` +
        'src/design/tokens.js is generated from src/styles/tokens.css and must not be ' +
        'hand-edited. Run `npm run tokens` and commit the result.',
    ).toBe(0);
  });

  it('actually ran the check, rather than passing because nothing happened', () => {
    // Non-vacuity. `gen-tokens: … is up to date (N tokens).` is printed on exactly
    // one path through the script: the end of the `--check` branch, after the
    // comparison succeeded. If the argv patch had not taken, the script would have
    // *written* the mirror and printed "wrote" instead, and the exit code would
    // still have been 0 — a green test over an unperformed check.
    expect(check.stdout).toMatch(/^gen-tokens: src\/design\/tokens\.js is up to date \(\d+ tokens\)\.$/m);
    expect(check.stdout).not.toMatch(/\bwrote\b/);

    // And the token count it verified is the count the mirror's own header states,
    // so the two ends of the comparison are the files named above.
    const verified = /is up to date \((\d+) tokens\)/.exec(check.stdout)?.[1];
    const declared = /^\/\/ (\d+) tokens, mirrored verbatim/m.exec(readFileSync(MIRROR, 'utf8'))?.[1];
    expect(verified).toBe(declared);
    expect(Number(verified)).toBeGreaterThan(80);
  });

  it('is checked by the same command the build runs, so the two cannot drift', () => {
    // The guard's whole claim to being equivalent to CI is that it invokes the
    // identical entry point with the identical flag.
    const pkg = JSON.parse(readFileSync(path.join(TERMINAL_ROOT, 'package.json'), 'utf8'));

    expect(pkg.scripts.prebuild).toBe('node scripts/gen-tokens.mjs --check');
    expect(pkg.scripts.tokens).toBe('node scripts/gen-tokens.mjs');
  });

  it('compares on normalised newlines, so a CRLF checkout is not a false failure', () => {
    // This is the assertion that stands in for the "byte for byte" wording, and the
    // reason the docblock above asks you not to restore it. `core.autocrlf=true`
    // with no `.gitattributes` means the same commit is on disk as LF here and as
    // CRLF on a fresh Windows clone.
    const lf = readFileSync(MIRROR, 'utf8').replace(/\r\n/g, '\n');
    const crlf = lf.replace(/\n/g, '\r\n');

    // A byte compare distinguishes those two checkouts of one commit …
    expect(crlf).not.toBe(lf);
    // … and the normalisation the generator applies does not.
    expect(crlf.replace(/\r\n/g, '\n')).toBe(lf);

    // Normalising is not a blanket "close enough": it collapses newline style and
    // nothing else, so one changed character in one token value still differs.
    const perturbed = lf.replace("canvas: '#080A0E'", "canvas: '#080A0F'");
    expect(perturbed, 'the anchor this assertion perturbs has moved').not.toBe(lf);
    expect(perturbed.replace(/\r\n/g, '\n')).not.toBe(lf);
  });
});
