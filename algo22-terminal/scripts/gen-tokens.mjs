#!/usr/bin/env node
/**
 * scripts/gen-tokens.mjs — generates src/design/tokens.js from src/styles/tokens.css.
 *
 * Requirement 1.1 (design.md §3.1, §3.3): tokens.css is the sole token source.
 * This script is the only thing permitted to write src/design/tokens.js.
 * Zero dependencies.
 *
 *   node scripts/gen-tokens.mjs           write the mirror
 *   node scripts/gen-tokens.mjs --check   exit 1 if the committed mirror is stale
 *
 * Parsing notes:
 * - The theme block is located by `@theme static {` OR `@theme {` — tokens.css
 *   declares `static` deliberately (tailwindcss 4.2.4 tree-shook 56 of 89
 *   tokens out of plain `@theme`), and both forms must keep working.
 * - Declarations are matched individually, not line by line: several tokens in
 *   tokens.css sit two-per-line (`--color-status-live: X;  --color-status-live-wash: Y;`).
 * - Values may contain commas and parentheses (rgba(), cubic-bezier(), font
 *   stacks with quoted family names); the value pattern stops only at `;`.
 * - A token matching no group rule is a hard error, so a new namespace added to
 *   tokens.css cannot silently go missing from the JS mirror.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, resolve, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = resolve(ROOT, 'src/styles/tokens.css');
const OUT = resolve(ROOT, 'src/design/tokens.js');

const fail = (msg) => {
  console.error(`gen-tokens: ${msg}`);
  process.exit(1);
};

/**
 * [cssPrefix, jsGroup, shape]. First match wins, so longer prefixes come first.
 * A dotted jsGroup nests: 'compat.bg' → token.compat.bg.*.
 */
const GROUPS = [
  ['--color-surface-', 'surface', 'flat'],
  ['--color-line-', 'line', 'flat'],
  ['--color-content-', 'content', 'flat'],
  ['--color-status-', 'status', 'pair'],
  ['--color-env-', 'env', 'pair'],
  ['--color-brand-', 'brand', 'flat'],
  ['--color-brand', 'brand', 'flat'], // bare token → brand.base

  // ── Deprecated compatibility aliases ─────────────────────────────────────
  // Every name under these four prefixes is an OLD name that tokens.css keeps
  // alive as a pure var() reference so pages this initiative does not redesign
  // keep compiling (see the COMPATIBILITY ALIASES section of tokens.css). None
  // of them is a canonical namespace: there is no --color-accent-*, --color-bg-*,
  // --color-text-* or --color-border-* token that declares a value. They are
  // quarantined under token.compat.* so a JS reader can see at a glance that a
  // name is scheduled for deletion, and so the whole group can be dropped in
  // one edit when the last consumer is redesigned.
  ['--color-accent-', 'compat.accent', 'flat'],
  ['--color-text-', 'compat.text', 'flat'],
  ['--color-bg-', 'compat.bg', 'flat'],
  ['--color-border-', 'compat.border', 'flat'],
  ['--color-border', 'compat.border', 'flat'], // bare token → compat.border.base

  ['--font-', 'font', 'flat'],
  ['--text-', 'text', 'text'],
  ['--spacing-', 'space', 'flat'],
  ['--radius-', 'radius', 'flat'],
  ['--shadow-', 'shadow', 'flat'],
  ['--transition-', 'transition', 'flat'],
  ['--breakpoint-', 'breakpoint', 'flat'],
  ['--focus-ring-', 'focusRing', 'flat'],
  ['--z-', 'zIndex', 'flat'],
];

const camel = (s) => s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());

// ── Parse ──────────────────────────────────────────────────────────────────
const css = readFileSync(SRC, 'utf8');
const open = /@theme\s+(?:static\s+)?\{/.exec(css);
if (!open) fail(`no @theme block found in ${SRC}`);

let depth = 1;
let i = open.index + open[0].length;
const blockStart = i;
while (i < css.length && depth > 0) {
  if (css[i] === '{') depth += 1;
  else if (css[i] === '}') depth -= 1;
  i += 1;
}
if (depth !== 0) fail('unterminated @theme block');
const block = css.slice(blockStart, i - 1).replace(/\/\*[\s\S]*?\*\//g, '');

const raw = new Map();
for (const [, name, value] of block.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
  if (raw.has(name)) fail(`duplicate declaration of ${name}`);
  raw.set(name, value.trim().replace(/\s+/g, ' '));
}
if (raw.size === 0) fail('@theme block declared no tokens');

/**
 * Resolve `var(--other)` aliases to their literal.
 *
 * DECISION: the JS mirror carries the RESOLVED LITERAL, not the var() reference.
 * tokens.css keeps a block of deprecated compatibility aliases so the pages this
 * initiative does not redesign keep compiling (design.md §3.2), but a JS consumer
 * reading token.compat.accent.profit needs a usable colour — a 'var(--…)' string
 * only works when assigned into a style attribute, and would break any consumer
 * doing colour maths or writing to a canvas.
 */
const deref = (name) => {
  let value = raw.get(name);
  const seen = new Set([name]);
  let m;
  while ((m = /^var\(\s*(--[\w-]+)\s*\)$/.exec(value))) {
    if (seen.has(m[1])) fail(`cyclic alias chain at ${name}`);
    if (!raw.has(m[1])) fail(`${name} aliases ${m[1]}, which tokens.css does not declare`);
    seen.add(m[1]);
    value = raw.get(m[1]);
  }
  return value;
};

// ── Group ──────────────────────────────────────────────────────────────────
const tree = {};
const put = (path, value) => {
  let node = tree;
  for (const key of path.slice(0, -1)) node = node[key] ??= {};
  node[path.at(-1)] = value;
};

for (const name of raw.keys()) {
  const hit = GROUPS.find(([prefix]) => name.startsWith(prefix));
  if (!hit) fail(`${name} matches no group; add a rule to GROUPS in scripts/gen-tokens.mjs`);
  const [prefix, group, shape] = hit;
  const rest = name.slice(prefix.length);

  // Requirement 1.1: a compatibility alias may only POINT at a canonical token.
  // The moment one is given a literal there are two declaration sites for the
  // same value and they can drift, which is the defect this file exists to
  // remove. Structural, not a comment.
  if (group.startsWith('compat.') && !/^var\(\s*--[\w-]+\s*\)$/.test(raw.get(name))) {
    fail(
      `${name} is a compatibility alias but declares the literal '${raw.get(name)}'. ` +
        'Aliases must be `var(--canonical-token)`.',
    );
  }

  const value = deref(name);
  const at = group.split('.'); // 'compat.bg' → ['compat', 'bg']

  if (shape === 'pair') {
    // status/env entries are { fg, wash } — design.md §4.1's semantic.js reads
    // token.status.profit.fg and .wash, so this shape is load-bearing.
    const wash = rest.endsWith('-wash');
    put([...at, camel(wash ? rest.slice(0, -'-wash'.length) : rest), wash ? 'wash' : 'fg'], value);
  } else if (shape === 'text' && rest.includes('--')) {
    // --text-figure--line-height → text.lineHeight.figure (suffix kept intact,
    // not mangled into a key fragment).
    const [key, modifier] = rest.split('--');
    put([...at, camel(modifier), camel(key)], value);
  } else {
    put([...at, rest === '' ? 'base' : camel(rest)], value);
  }
}

// ── Emit ───────────────────────────────────────────────────────────────────
const ident = (k) => (/^[A-Za-z_$][\w$]*$/.test(k) ? k : `'${k}'`);
const quote = (v) => (v.includes("'") ? JSON.stringify(v) : `'${v}'`);

const serialise = (node, depthLevel) => {
  const pad = '  '.repeat(depthLevel);
  const keys = Object.keys(node);
  // Scalars before nested groups, so a sub-group never lands mid-list.
  const ordered = [
    ...keys.filter((k) => typeof node[k] === 'string'),
    ...keys.filter((k) => typeof node[k] !== 'string'),
  ];
  return ordered
    .map((k) =>
      typeof node[k] === 'string'
        ? `${pad}${ident(k)}: ${quote(node[k])},`
        : `${pad}${ident(k)}: Object.freeze({\n${serialise(node[k], depthLevel + 1)}\n${pad}}),`,
    )
    .join('\n');
};

const output = `// AUTO-GENERATED by scripts/gen-tokens.mjs from src/styles/tokens.css.
// DO NOT EDIT. Run \`npm run tokens\` after changing tokens.css.
// CI fails if this file is stale (see design.md §15).
//
// ${raw.size} tokens, mirrored verbatim as strings. var() aliases are resolved to
// their literal here so JS consumers get a usable value; tokens.css keeps the
// indirection for CSS consumers.
export const token = Object.freeze({
${serialise(tree, 1)}
});

/**
 * CSS custom-property reference, for inline styles during migration.
 *
 * The path is the CSS custom-property path with dots for dashes, not a path
 * into \`token\` above: cssVar('color.status.profit') → 'var(--color-status-profit)'.
 */
export const cssVar = (path) => \`var(--\${path.replace(/\\./g, '-')})\`;
`;

// ── Write or check ─────────────────────────────────────────────────────────
const where = relative(ROOT, OUT).replace(/\\/g, '/');
if (process.argv.includes('--check')) {
  if (!existsSync(OUT)) fail(`${where} does not exist. Run \`npm run tokens\`.`);
  // core.autocrlf checkouts rewrite LF to CRLF on disk, so compare on
  // normalised newlines. Every real difference — value, key, group, order —
  // still fails.
  if (readFileSync(OUT, 'utf8').replace(/\r\n/g, '\n') !== output) {
    fail(`${where} is stale relative to src/styles/tokens.css. Run \`npm run tokens\`.`);
  }
  console.log(`gen-tokens: ${where} is up to date (${raw.size} tokens).`);
} else {
  mkdirSync(dirname(OUT), { recursive: true });
  writeFileSync(OUT, output);
  console.log(`gen-tokens: wrote ${where} (${raw.size} tokens).`);
}
