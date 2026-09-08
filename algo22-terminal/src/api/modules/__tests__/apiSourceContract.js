/**
 * __tests__/apiSourceContract.js — shared helpers for the API module contract tests.
 *
 * Not a test file itself (no `.test.`/`.spec.` in the name, so vitest does not collect it).
 * It holds the two things `libraryApi.test.js` and `paperApi.test.js` must do identically,
 * because a drifting copy of either would quietly stop enforcing the rule:
 *
 *  1. `collectMethodPaths` — a reflective walk of an exported API object, so the set of
 *     methods a test covers is compared against the set the module actually exposes. A method
 *     added to the module without a case in the expectation table fails the suite instead of
 *     passing silently.
 *  2. `readModuleSource` / `assertTravelsOnlyTheSharedClient` — the **source text** check.
 *     Reflection over the live functions cannot see a hard-coded host or an `import.meta.env`
 *     read that never happens to be reached, so the rule in Requirement 20.2 (no own HTTP
 *     client, no own base URL, no hard-coded host) is asserted against the bytes on disk.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect } from 'vitest';

/** `algo22-terminal/src/api/modules/` — the directory holding the modules under test. */
export const MODULES_DIR = path.dirname(fileURLToPath(import.meta.url)).replace(
  /[\\/]__tests__$/,
  '',
);

/** The repository root, five levels above this file (`modules/api/src/algo22-terminal/`). */
export const REPO_ROOT = path.resolve(MODULES_DIR, '..', '..', '..', '..');

/**
 * Read one API module's source text off disk.
 *
 * @param {string} fileName - e.g. `'library.js'`.
 * @returns {string}
 */
export const readModuleSource = (fileName) =>
  fs.readFileSync(path.join(MODULES_DIR, fileName), 'utf8');

/**
 * Remove comments so a word that appears only in prose is not read as code.
 *
 * `paper.js` documents that another tenant's session "is never fetched rather than fetched and
 * dropped" — a bare search for `fetch` in the raw text would fail on the documentation. The
 * `[^:]` guard on the line-comment rule deliberately leaves `http://` and `https://` intact,
 * since in both a comment and a string literal those slashes are preceded by a colon; that
 * keeps an absolute URL visible to the check below rather than eaten as a comment.
 *
 * @param {string} source
 * @returns {string}
 */
export const stripComments = (source) =>
  source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:])\/\/[^\n]*/gm, '$1');

/** Every construct that would mean the module is not travelling the shared client. */
const FORBIDDEN_IN_CODE = [
  ['import.meta.env', /import\.meta\.env/],
  ['fetch(', /\bfetch\s*\(/],
  ['XMLHttpRequest', /\bXMLHttpRequest\b/],
  ['axios (including axios.create)', /\baxios\b/],
  ['an absolute http:// or https:// URL', /https?:\/\//],
  ['WebSocket', /\bnew\s+WebSocket\b/],
];

/**
 * Assert a module builds no transport of its own.
 *
 * Checked against the module's source text, not its functions: a hard-coded host inside a
 * branch no test happens to take is exactly the defect this is here to catch. Every `from`
 * specifier is checked too — a module that imports anything other than the shared client has
 * acquired a second way to reach the network.
 *
 * @param {string} fileName - e.g. `'library.js'`.
 * @param {string} [expectedImport='../../apiClient'] - The only permitted import specifier.
 */
export const assertTravelsOnlyTheSharedClient = (
  fileName,
  expectedImport = '../../apiClient',
) => {
  const raw = readModuleSource(fileName);
  const code = stripComments(raw);

  for (const [label, pattern] of FORBIDDEN_IN_CODE) {
    expect(
      pattern.test(code),
      `${fileName} must not reference ${label}; it travels ${expectedImport} like every other module`,
    ).toBe(false);
  }

  const specifiers = [...code.matchAll(/\bfrom\s+['"]([^'"]+)['"]/g)].map((m) => m[1]);
  expect(specifiers.length, `${fileName} must import the shared client`).toBeGreaterThan(0);
  for (const specifier of specifiers) {
    expect(
      specifier,
      `${fileName} imports '${specifier}'; only '${expectedImport}' is permitted`,
    ).toBe(expectedImport);
  }
};

/**
 * Every callable on an exported API object, as dotted paths, sorted.
 *
 * Nested plain objects (`libraryApi.submissions`, `paperApi.sessions`) are namespaces and are
 * walked into; anything callable is a method. The point of returning the whole set is that a
 * test can compare it against its expectation table, so a newly added method that nothing
 * covers makes the suite fail rather than pass silently.
 *
 * @param {Object} target
 * @param {string} [prefix='']
 * @returns {string[]}
 */
export const collectMethodPaths = (target, prefix = '') => {
  const found = [];
  for (const [key, value] of Object.entries(target)) {
    const dotted = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'function') {
      found.push(dotted);
    } else if (value !== null && typeof value === 'object') {
      found.push(...collectMethodPaths(value, dotted));
    }
  }
  return found.sort();
};

/**
 * Resolve a dotted method path against an API object.
 *
 * @param {Object} target
 * @param {string} dotted - e.g. `'submissions.setPrice'`.
 * @returns {Function}
 */
export const methodAt = (target, dotted) =>
  dotted.split('.').reduce((node, key) => node[key], target);
