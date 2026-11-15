/**
 * ═══════════════════════════════════════════════════════════════════════════
 * strategyBuilder.layout.test.js — LAYOUT-01, the in-shell viewport-height rule
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * THE DEFECT THIS PINS (reported and fixed 2026-11-15)
 * ---------------------------------------------------------------------------
 * The Strategy Builder showed a large empty band between its toolbar and the canvas, and
 * the band's apparent size changed with browser zoom.
 *
 * `src/pages/StrategyBuilder.jsx` opened with `height: '100vh'` on its root. That element
 * does not own the viewport. Both routes that mount the builder — `/app/builder` in
 * `App.jsx` and the `view === 'builder'` branch of `pages/Strategies.jsx` — render it inside
 * the authenticated shell's `<main>`, which `App.jsx` declares as the `1fr` row of a
 * `min-content 1fr` grid. The height available to a page is therefore the viewport MINUS the
 * TopBar row and its disconnected-state strip.
 *
 * So the column was taller than its container by exactly the height of the chrome above it.
 * The toolbar sat at the top of the over-tall column, the canvas below it is a `flex: 1`
 * child measured against the over-tall box, and the surplus rendered as the empty band plus
 * a document-level scroll. The zoom dependence is the same fact seen from another angle:
 * zoom changes the CSS-pixel height of the fixed chrome but not the definition of `vh`, so
 * the mismatch grows and shrinks with the zoom level rather than staying put.
 *
 * WHY A SOURCE TEST AND NOT A RENDER TEST
 * ---------------------------------------------------------------------------
 * jsdom performs no layout: every element reports a zero height and `getComputedStyle`
 * resolves no percentages. A rendered assertion could only read back the same inline style
 * this file reads from source, while paying for the full builder tree. It would look like a
 * behavioural test and be a string comparison with extra steps. The actual geometry belongs
 * in the Playwright sweep under `tests/e2e/`, which has a real viewport and can be run at
 * several sizes and zoom levels.
 *
 * What a source test CAN hold, and what the bug actually needed, is the invariant: an
 * element that renders inside `<main>` must not size itself against the viewport.
 *
 * THE RULE
 * ---------------------------------------------------------------------------
 * Modules rendered ONLY inside the shell must contain no `vh`/`dvh` length anywhere. Not
 * "on the root" — anywhere — because the root is not the only element that can overflow the
 * column, and a test that tried to identify "the root" by parsing JSX would be guessing.
 *
 * Shell-internal pages express fill as:
 *   - `flex: 1` + `minHeight: 0`   for a surface that must fill exactly and scroll inside
 *                                   itself (the builder: a canvas app)
 *   - `minHeight: '100%'`          for an ordinary scrolling page, letting `<main>`'s own
 *                                   `overflowY: 'auto'` do the scrolling
 *
 * `minHeight: 0` is not decoration in the first form. A flex item defaults to
 * `min-height: auto` and refuses to shrink below its content, which is precisely what lets
 * an over-tall child push the document into a scroll instead of letting the canvas size
 * itself to what is visible.
 */

import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

import { SRC, stripComments } from '../guards/source-scan.js';

/**
 * Modules that render ONLY inside the authenticated shell's `<main>`, and so may never
 * size themselves against the viewport.
 *
 * Deliberately NOT every file under `src/pages/`. Membership here is a claim about where a
 * module mounts, and that claim is checked against `App.jsx` by
 * `only lists modules that App.jsx mounts inside the shell` below — so this list cannot
 * quietly come to include a page that also renders standalone.
 */
const IN_SHELL_ONLY = [
  'pages/StrategyBuilder.jsx',
  'pages/StrategyDetail.jsx',
  'components/NotificationCenter.jsx',
];

/**
 * Modules that mount in BOTH contexts and are therefore out of scope here.
 *
 * `TwoFA.jsx` is the real one: `App.jsx` serves it at `/2fa` (public, outside the shell,
 * where it must fill the viewport) and at `/app/2fa` (inside `<main>`, where `100vh`
 * overflows by the chrome height). It still carries `minHeight: '100vh'`, so the in-shell
 * instance has the LAYOUT-01 defect.
 *
 * It is listed rather than fixed because the fix is not the same one-line change: `100%`
 * would resolve against the public route's wrapper, whose height this change set did not
 * establish, and guessing it would risk breaking the standalone two-factor screen — the one
 * surface a user cannot route around if it renders wrong. Fixing it means either splitting
 * the layout shell from the form, or giving the public wrapper a definite height. Tracked in
 * docs/audits/VYOMQUANT_MASTER_ENGINEERING_AUDIT.md §5.1.
 *
 * This constant exists so the exception is recorded in the guard that would otherwise catch
 * it, instead of the guard silently not covering it.
 */
const DUAL_CONTEXT_DEFERRED = ['pages/TwoFA.jsx'];

/** Any `vh` or `dvh` length: `100vh`, `50dvh`, `calc(100vh - 64px)`. */
const VIEWPORT_UNIT = /\b\d+(?:\.\d+)?d?vh\b/;

const read = (relative) => fs.readFileSync(path.join(SRC, relative), 'utf8');

/**
 * Source with comments removed but STRING LITERALS INTACT.
 *
 * `codeOnly()` is the wrong helper here and the distinction matters: it also masks string
 * spans, and a CSS length in this codebase *is* a string (`height: '100vh'`). Masking
 * strings would make every assertion below pass against a file still full of `100vh`.
 * Comments must go, though — this file and the three it checks all discuss `100vh` in prose.
 */
const codeWithStrings = (relative) => stripComments(read(relative));

describe('LAYOUT-01: shell-internal modules do not size against the viewport', () => {
  it.each(IN_SHELL_ONLY)('%s contains no vh/dvh length', (relative) => {
    const code = codeWithStrings(relative);
    const offenders = code
      .split('\n')
      .map((line, i) => [i + 1, line])
      .filter(([, line]) => VIEWPORT_UNIT.test(line));

    expect(
      offenders,
      `${relative} renders inside the shell's <main>, where the available height is the ` +
        'viewport minus the TopBar and its strip. A vh/dvh length here overflows the ' +
        'document by exactly the chrome height — the LAYOUT-01 defect. Use `flex: 1` + ' +
        '`minHeight: 0` to fill exactly, or `minHeight: \'100%\'` for a scrolling page.\n' +
        offenders.map(([n, line]) => `  ${relative}:${n}  ${line.trim()}`).join('\n'),
    ).toEqual([]);
  });

  it('the builder root fills its flex parent and may shrink', () => {
    const code = codeWithStrings('pages/StrategyBuilder.jsx');

    // The builder's outermost element, identified by the one property that has always been
    // on it and nothing else in the file: the panel background on a column flex container.
    const root = code
      .split('\n')
      .find((line) => line.includes("flexDirection: 'column'") && line.includes('token.surface.panel'));

    expect(root, 'could not find the StrategyBuilder root element').toBeTruthy();
    expect(root, 'the builder is a flex item of <main> and must grow to fill it').toContain('flex: 1');
    expect(
      root,
      "a flex item defaults to `min-height: auto` and will not shrink below its content, " +
        'which is how an over-tall child scrolls the document instead of letting the canvas ' +
        'fit the visible area',
    ).toContain('minHeight: 0');
  });

  it('only lists modules that App.jsx mounts inside the shell', () => {
    const app = codeWithStrings('App.jsx');

    for (const relative of IN_SHELL_ONLY) {
      const component = path.basename(relative, path.extname(relative));
      // Every shell route is `/app/...`; a standalone mount would be a route without that
      // prefix referencing the same component. `TwoFA` is the known dual mount and is
      // excluded from IN_SHELL_ONLY for exactly this reason.
      const standalone = new RegExp(`path="/(?!app)[^"]*"[^\\n]*<${component}\\b`);
      expect(
        standalone.test(app),
        `${relative} is also mounted on a non-/app route, so it is not shell-only and the ` +
          'vh rule above does not apply to it unconditionally. Move it to ' +
          'DUAL_CONTEXT_DEFERRED and give it a context-aware fix.',
      ).toBe(false);
    }
  });

  it('records TwoFA as a known, unfixed dual-context instance', () => {
    // Guards must not imply coverage they do not have. If someone fixes TwoFA, this fails
    // and the exception should be deleted — which is the intended way to retire it.
    for (const relative of DUAL_CONTEXT_DEFERRED) {
      expect(
        VIEWPORT_UNIT.test(codeWithStrings(relative)),
        `${relative} no longer uses a viewport unit. Remove it from DUAL_CONTEXT_DEFERRED ` +
          'and add it to IN_SHELL_ONLY if it is now shell-only.',
      ).toBe(true);
    }
  });
});

describe('LAYOUT-01: the shell contract the fix depends on', () => {
  /*
    The builder's `flex: 1` + `minHeight: 0` is only correct because `<main>` is a
    column flex container with a bounded height. If the shell stops being that, the builder
    does not fail loudly — it goes back to the original symptom. So the dependency is pinned
    here rather than left as a comment.
  */
  const mainBlock = () => {
    const lines = codeWithStrings('App.jsx').split('\n');
    const start = lines.findIndex((line) => line.includes('id="main-content"'));
    expect(start, 'the <main id="main-content"> landmark is gone from App.jsx').toBeGreaterThan(-1);
    // The style object follows the opening tag; 60 lines covers it and its comments.
    return lines.slice(start, start + 60).join('\n');
  };

  it('<main> is a column flex container', () => {
    expect(mainBlock()).toContain("flexDirection: 'column'");
  });

  it('<main> may shrink below its content', () => {
    expect(
      mainBlock(),
      'without `minHeight: 0` the grid row refuses to shrink and the page scrolls the ' +
        'document — the guard App.jsx already documents for the horizontal axis',
    ).toContain('minHeight: 0');
  });

  it('<main> owns the vertical scroll for authenticated pages', () => {
    expect(
      mainBlock(),
      'shell-internal pages size themselves to `<main>` and rely on it to scroll; if it ' +
        'stops scrolling, every `minHeight: \'100%\'` page clips instead',
    ).toContain("overflowY: 'auto'");
  });
});
