/**
 * The `C` compatibility shim contract — design.md §3.4, Requirements 1.1, 1.4, 1.5.
 *
 * `C` is no longer a token source; it is a frozen projection of
 * `src/design/tokens.js`, which is generated from `src/styles/tokens.css`. These
 * tests assert the three properties that make that claim checkable:
 *
 *   1. Every value is a value that exists in `token` (nothing was hand-written).
 *   2. Its key set is exactly the pre-migration inventory — the ~700 existing
 *      `C.*` call sites keep resolving, and the shim did not grow.
 *   3. `glow.*` and `gradient.*` are all `'none'`, which is how Requirement 1.5
 *      is satisfied app-wide without touching a page.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, it, expect } from 'vitest';

import { C } from '../../src/components/ui-legacy/primitives.jsx';
import { token } from '../../src/design/tokens.js';

/**
 * The key set `C` carried before it became derived, transcribed from the
 * pre-migration object. This is the "do not add, remove or rename a key"
 * assertion: only the values were allowed to change in this step.
 */
const KEYS = [
  'bg', 'bg0', 'bg1', 'bg2', 'bg3', 'bg4',
  'border', 'borderLight', 'borderHover',
  'cyan', 'cyanL', 'cyanD', 'cyanDim', 'accent', 'accentHover', 'accentMuted', 'blue',
  'profit', 'green', 'greenD', 'profitDark', 'profitBg',
  'loss', 'red', 'lossDark', 'lossBg',
  't1', 't2', 't3', 't4',
  'warning', 'gold', 'purple', 'orange',
  'shadow', 'shadowMd', 'shadowLg',
  'glow', 'gradient', 'space', 'radius',
];

/** Every string leaf reachable from `token`, i.e. the set of legal `C` colours. */
const tokenValues = (node, acc = new Set()) => {
  for (const value of Object.values(node)) {
    if (typeof value === 'string') acc.add(value);
    else if (value && typeof value === 'object') tokenValues(value, acc);
  }
  return acc;
};

describe('C legacy token shim', () => {
  it('exposes exactly the pre-migration key inventory', () => {
    expect(Object.keys(C).sort()).toEqual([...KEYS].sort());
  });

  it('is frozen, top level and in every nested group', () => {
    expect(Object.isFrozen(C)).toBe(true);
    for (const group of ['glow', 'gradient', 'space', 'radius']) {
      expect(Object.isFrozen(C[group]), group).toBe(true);
    }
  });

  it('derives every colour, shadow and wash from the generated tokens', () => {
    const legal = tokenValues(token);
    const derived = KEYS.filter((key) => typeof C[key] === 'string');

    // Sanity: the colour/shadow keys are the ones being checked, not zero of them.
    expect(derived.length).toBe(KEYS.length - 4);

    for (const key of derived) {
      expect(legal.has(C[key]), `C.${key} = ${C[key]} is not a token value`).toBe(true);
    }
  });

  it('collapses the pairs §3.4 declares redundant', () => {
    expect(C.bg).toBe(C.bg0);
    expect(C.bg3).toBe(C.bg4);
    expect(C.borderLight).toBe(C.borderHover);
    // One amber for warning, gold and orange; #FFB74D is retired.
    expect(C.warning).toBe(token.status.warning.fg);
    expect(C.gold).toBe(C.warning);
    expect(C.orange).toBe(C.warning);
    // The decorative hues leave the palette.
    expect(C.purple).toBe(token.status.neutral.fg);
    expect(C.blue).toBe(token.brand.base);
    // t3/t4 both become the non-text-only muted content token.
    expect(C.t3).toBe(token.content.muted);
    expect(C.t4).toBe(C.t3);
  });

  it('maps the semantic status colours through the one status mapping', () => {
    expect(C.profit).toBe(token.status.profit.fg);
    expect(C.green).toBe(token.status.profit.fg);
    expect(C.profitBg).toBe(token.status.profit.wash);
    expect(C.loss).toBe(token.status.loss.fg);
    expect(C.red).toBe(token.status.loss.fg);
    expect(C.lossBg).toBe(token.status.loss.wash);
  });

  it("retires every glow and gradient to 'none' — Requirement 1.5", () => {
    expect(Object.values(C.glow).every((v) => v === 'none')).toBe(true);
    expect(Object.values(C.gradient).every((v) => v === 'none')).toBe(true);
  });

  it('keeps space and radius as unitless px numbers matching the token scale', () => {
    // Numeric, because `C.space.*` is consumed arithmetically
    // (`clientWidth - 2 * C.space.xl`) and interpolated (`${C.space.sm}px 0`).
    for (const scale of ['space', 'radius']) {
      for (const [key, value] of Object.entries(C[scale])) {
        expect(Number.isFinite(value), `C.${scale}.${key}`).toBe(true);
      }
    }

    expect(C.space).toEqual({ xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 24 });
    expect(C.radius).toEqual({ sm: 4, md: 6, lg: 8, xl: 12 });

    // ...and those numbers are the token rem/px values, not a second scale.
    expect(C.space.md).toBe(parseFloat(token.space['3']) * 16);
    expect(C.radius.md).toBe(parseInt(token.radius.md, 10));
  });
});
/**
 * The two pages that used to declare a competing `C` — design.md §3.4, §17.2,
 * Requirement 1.1. They are out of scope for redesign, so the only claim under
 * test is the token *source*: they hold no local palette, they import the shim,
 * and every key they read off it resolves to a real value rather than
 * `undefined`. The shim is frozen and closed, so a reference the shim cannot
 * answer has to be caught here rather than papered over with a new key.
 */
const MIGRATED_PAGES = ['SupportCenter.jsx', 'NotificationCenter.jsx'];

/**
 * Resolved from `__dirname`, not from `new URL(…, import.meta.url)`.
 *
 * That second form does not mean what it means in plain Node here: Vite's
 * `asset-import-meta-url` plugin pattern-matches the literal
 * `new URL(<specifier>, import.meta.url)` expression during transform and
 * rewrites it into an *asset* lookup, which yields a server-root-relative path
 * (`/src/components/SupportCenter.jsx`). Joined back onto a `file:` base that
 * resolves to the drive root — `C:\src\components\…` — so every read raised
 * ENOENT at collection time and the whole file reported "0 tests" instead of
 * failing loudly. `__dirname` is injected by the runner, is a real filesystem
 * path, and is untouched by any transform; it is what every other source-reading
 * suite here (`builder.architecture`, `strategyRename`, `nodeTrace`) already uses.
 */
const pageSource = (file) =>
  readFileSync(path.resolve(__dirname, '..', '..', 'src', 'components', file), 'utf8');

/** Every distinct `C.<key>` / `C.<group>.<key>` path the file actually reads. */
const referencedPaths = (source) =>
  [...source.matchAll(/\bC\.([A-Za-z_$][\w$]*)(?:\.([A-Za-z_$][\w$]*))?/g)].map((m) =>
    m[2] ? [m[1], m[2]] : [m[1]],
  );

describe.each(MIGRATED_PAGES)('%s reads the shim, not a local palette', (file) => {
  const source = pageSource(file);

  it('declares no local C object', () => {
    expect(source).not.toMatch(/\bconst\s+C\s*=/);
  });

  it('imports C from the shim', () => {
    expect(source).toMatch(/import\s*\{[^}]*\bC\b[^}]*\}\s*from\s*['"]\.\/ui-legacy\/primitives['"]/);
  });

  it('resolves every C reference it makes to a defined value', () => {
    const paths = referencedPaths(source);
    expect(paths.length).toBeGreaterThan(0);

    // `keys`, not `path` — that name is the `node:path` import at module scope.
    for (const keys of paths) {
      const value = keys.reduce((node, key) => (node == null ? undefined : node[key]), C);
      expect(value, `C.${keys.join('.')} is undefined in ${file}`).toBeDefined();
    }
  });
});
