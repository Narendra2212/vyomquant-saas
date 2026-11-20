/**
 * THEME-01 — the theme system's structural guarantees.
 *
 * These are the assertions that cannot be made by looking at a screenshot, and
 * each one corresponds to a way light mode would break SILENTLY:
 *
 *   * a colour declared in the dark block but not the light one falls back to
 *     the dark value, so one panel stays black on a white page and nothing
 *     errors;
 *   * a hex-alpha concatenation (`${token.brand.base}15`) now produces
 *     `var(--color-brand)15`, which is not a colour, so the browser DROPS the
 *     declaration — no error, just a missing background;
 *   * a colour token emitted as a literal instead of a `var()` reference simply
 *     ignores the theme;
 *   * `public/theme-boot.js` duplicates the storage key and the chrome colours
 *     because it must run before the bundle exists, and a drift between it and
 *     `design/theme.jsx` shows up as a flash or a wrong chrome colour, not as a
 *     failure.
 *
 * The first four read source files rather than a rendered DOM, because that is
 * where those defects live.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const ROOT = resolve(__dirname, '../../..');
const read = (rel) => readFileSync(resolve(ROOT, rel), 'utf8');

/** Every .js/.jsx under `dir`, as paths relative to ROOT. Node 20 compatible. */
function walk(dir) {
  const out = [];
  for (const entry of readdirSync(resolve(ROOT, dir), { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) out.push(...walk(rel));
    else if (/\.jsx?$/.test(entry.name)) out.push(rel);
  }
  return out;
}

const TOKENS_CSS = read('src/styles/tokens.css');
const TOKENS_JS = read('src/design/tokens.js');
const THEME_BOOT = read('public/theme-boot.js');
const THEME_JSX = read('src/design/theme.jsx');
const INDEX_HTML = read('index.html');

/** Declarations inside a `{ … }` block, as a Map of name → value. */
function declarations(block) {
  const out = new Map();
  for (const [, name, value] of block.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    out.set(name, value.trim().replace(/\s+/g, ' '));
  }
  return out;
}

/** The body of a brace-balanced block whose opening is matched by `openRe`. */
function blockAfter(css, openRe) {
  const open = openRe.exec(css);
  if (!open) throw new Error(`block not found: ${openRe}`);
  let depth = 1;
  let i = open.index + open[0].length;
  const start = i;
  while (i < css.length && depth > 0) {
    if (css[i] === '{') depth += 1;
    else if (css[i] === '}') depth -= 1;
    i += 1;
  }
  return css.slice(start, i - 1).replace(/\/\*[\s\S]*?\*\//g, '');
}

const darkBlock = declarations(blockAfter(TOKENS_CSS, /@theme\s+(?:static\s+)?\{/));
const lightBlock = declarations(
  blockAfter(TOKENS_CSS, /\[data-theme=['"]light['"]\]\s*\{/),
);

/** A canonical colour: declares a value, and is not a `var()` alias. */
const isCanonicalColour = ([name, value]) =>
  name.startsWith('--color-') && !/^var\(\s*--[\w-]+\s*\)$/.test(value);

describe('the light theme covers the dark theme', () => {
  it('declares every canonical colour the dark theme declares', () => {
    const dark = [...darkBlock].filter(isCanonicalColour).map(([n]) => n);
    const missing = dark.filter((n) => !lightBlock.has(n));
    expect(
      missing,
      'these colours have no light value, so they fall back to the DARK value ' +
        'on a white page and nothing reports an error',
    ).toEqual([]);
  });

  it('declares no colour the dark theme does not have', () => {
    const darkNames = new Set([...darkBlock.keys()]);
    const extra = [...lightBlock.keys()].filter(
      (n) => n.startsWith('--color-') && !darkNames.has(n),
    );
    expect(
      extra,
      'a colour that exists only in light has no dark counterpart, so dark ' +
        'renders it as an invalid value',
    ).toEqual([]);
  });

  it('does not re-declare the compatibility aliases', () => {
    // Each alias is a pure var(--canonical) reference, so it follows the
    // overrides automatically. Re-stating one would create the second
    // declaration site tokens.css's own header forbids.
    const aliases = [...lightBlock].filter(([, v]) =>
      /^var\(\s*--[\w-]+\s*\)$/.test(v),
    );
    expect(aliases.map(([n]) => n)).toEqual([]);
  });

  it('overrides no non-colour token except the shadows', () => {
    // Spacing, radius, type scale, motion, breakpoints and z-index are
    // theme-independent. Shadows are the stated exception: a shadow IS an rgba
    // colour, and dark-theme shadows are invisible on white.
    const offenders = [...lightBlock.keys()].filter(
      (n) =>
        !n.startsWith('--color-') &&
        !n.startsWith('--shadow-') &&
        n !== '--focus-ring-color',
    );
    expect(offenders).toEqual([]);
  });
});

describe('the JS mirror is theme-aware', () => {
  it('emits every colour as a var() reference, never a literal', () => {
    const literals = [...TOKENS_JS.matchAll(/^\s*([\w'[\]]+):\s*'(#[0-9a-fA-F]{3,8})'/gm)];
    expect(
      literals.map((m) => `${m[1]} = ${m[2]}`),
      'a colour baked as a literal cannot follow the theme',
    ).toEqual([]);
  });

  it('keeps the non-colour tokens literal, because JS parses them as numbers', () => {
    // ResponsiveGate parses token.breakpoint.* into pixels and refuses a value
    // it cannot parse; PaperTrading computes parseFloat(token.space['5']) * 16.
    expect(TOKENS_JS).toMatch(/tablet: '768px'/);
    expect(TOKENS_JS).toMatch(/'5': '1\.25rem'/);
    expect(TOKENS_JS).not.toMatch(/tablet: 'var\(/);
  });
});

describe('no colour is made translucent by string concatenation', () => {
  it('has no hex-alpha concatenation left in src/', () => {
    // `${token.x.y}15` and `token.x.y + "15"` both produce `var(--…)15`, which
    // is invalid CSS that the browser drops without complaint.
    //
    // Walked with readdirSync rather than `fs.globSync`: that landed in Node 22
    // and CI pins NODE_VERSION 20, so it threw there while passing locally.
    const files = walk('src');
    const offenders = [];
    for (const rel of files) {
      const text = read(rel);
      text.split('\n').forEach((line, i) => {
        const s = line.trimStart();
        if (s.startsWith('*') || s.startsWith('//') || s.startsWith('/*')) return;
        if (/\$\{[^{}]*?token\.[^{}]*?\}[0-9a-fA-F]{2}\b/.test(line)) {
          offenders.push(`${rel}:${i + 1} (template)`);
        }
        if (/token(?:\.[A-Za-z]+)+\s*\+\s*["'][0-9a-fA-F]{2}["']/.test(line)) {
          offenders.push(`${rel}:${i + 1} (concat)`);
        }
      });
    }
    expect(
      offenders,
      'use alpha(colour, percent) from design/alpha.js instead',
    ).toEqual([]);
  });
});

describe('theme-boot.js and design/theme.jsx agree', () => {
  it('uses the same storage key', () => {
    const bootKey = /STORAGE_KEY\s*=\s*'([^']+)'/.exec(THEME_BOOT)?.[1];
    const jsxKey = /STORAGE_KEY\s*=\s*'([^']+)'/.exec(THEME_JSX)?.[1];
    expect(bootKey).toBeTruthy();
    expect(bootKey).toBe(jsxKey);
  });

  it('uses the same browser-chrome colour per theme', () => {
    const bootLight = /'light'\s*\?\s*'(#[0-9A-Fa-f]{6})'/.exec(THEME_BOOT)?.[1];
    const bootDark = /:\s*'(#[0-9A-Fa-f]{6})'/.exec(
      THEME_BOOT.slice(THEME_BOOT.indexOf("'light' ?")),
    )?.[1];
    const jsxLight = /light:\s*'(#[0-9A-Fa-f]{6})'/.exec(THEME_JSX)?.[1];
    const jsxDark = /dark:\s*'(#[0-9A-Fa-f]{6})'/.exec(THEME_JSX)?.[1];

    expect(bootLight?.toUpperCase()).toBe(jsxLight?.toUpperCase());
    expect(bootDark?.toUpperCase()).toBe(jsxDark?.toUpperCase());
  });

  it('those colours are the canvas colour of each theme', () => {
    const jsxLight = /light:\s*'(#[0-9A-Fa-f]{6})'/.exec(THEME_JSX)?.[1];
    const jsxDark = /dark:\s*'(#[0-9A-Fa-f]{6})'/.exec(THEME_JSX)?.[1];
    expect(jsxDark.toUpperCase()).toBe(
      darkBlock.get('--color-surface-canvas').toUpperCase(),
    );
    expect(jsxLight.toUpperCase()).toBe(
      lightBlock.get('--color-surface-canvas').toUpperCase(),
    );
  });

  it('treats dark as the absence of the attribute', () => {
    // So the @theme block stays the default for any document that never runs
    // this code: a Tauri splash, an error page, a component mounted in a test.
    expect(THEME_BOOT).toMatch(/removeAttribute\(['"]data-theme['"]\)/);
    expect(THEME_JSX).toMatch(/removeAttribute\(['"]data-theme['"]\)/);
  });
});

describe('the pre-paint script is loaded in a way that can work', () => {
  it('is a classic synchronous script in <head>', () => {
    const head = INDEX_HTML.slice(0, INDEX_HTML.indexOf('</head>'));
    expect(head).toMatch(/<script\s+src="\/theme-boot\.js"\s*>/);
    // `type="module"` or `defer` would make it run after the first paint, which
    // is the flash it exists to prevent.
    const tag = /<script[^>]*theme-boot\.js[^>]*>/.exec(INDEX_HTML)?.[0] ?? '';
    expect(tag).not.toMatch(/type=["']module["']/);
    expect(tag).not.toMatch(/\bdefer\b/);
    expect(tag).not.toMatch(/\basync\b/);
  });

  it('stays CSP-legal: a same-origin file, never inline', () => {
    // script-src carries no 'unsafe-inline' and a meta-tag CSP cannot carry a
    // nonce, so an inline theme snippet would simply not execute.
    const csp = /Content-Security-Policy[^>]*content="([\s\S]*?)"/.exec(INDEX_HTML)?.[1] ?? '';
    expect(csp).toMatch(/script-src[^;]*'self'/);
    expect(csp).not.toMatch(/script-src[^;]*'unsafe-inline'/);
  });
});
