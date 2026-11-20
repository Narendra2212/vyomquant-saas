/**
 * ═══════════════════════════════════════════════════════════════════════════
 * strategyBuilderLayout.spec.ts — LAYOUT-01 measured, not inferred (@layout)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * THE HANDOFF THIS COMPLETES
 * ---------------------------------------------------------------------------
 * `tests/unit/pages/strategyBuilder.layout.test.js` pins LAYOUT-01 as a SOURCE rule —
 * no `vh`/`dvh` length anywhere in a module that renders inside the shell — and says in
 * its own docstring why it cannot do more:
 *
 *     "jsdom performs no layout: every element reports a zero height and
 *      `getComputedStyle` resolves no percentages. […] The actual geometry belongs in
 *      the Playwright sweep under `tests/e2e/`, which has a real viewport and can be
 *      run at several sizes."
 *
 * This is that sweep. The two are not redundant: the source rule catches the *mechanism*
 * (an element sizing against the viewport) on every commit for nothing, and this catches
 * the *outcome* (a gap, or a column that does not fit) however it arises — including from
 * a grid row, a padding or a min-height that carries no `vh` at all.
 *
 * THE DEFECT
 * ---------------------------------------------------------------------------
 * The builder showed a large empty band between its toolbar and the canvas, and the band's
 * size changed with browser zoom. `StrategyBuilder.jsx` opened with `height: '100vh'`, but
 * it renders inside the shell's `<main>`, which is the `1fr` row of a `min-content 1fr`
 * grid — so the height available is the viewport MINUS the TopBar and its
 * disconnected-state strip. The column was taller than its container by exactly the height
 * of the chrome above it, and the surplus rendered as the band plus a document scroll.
 *
 * WHAT IS ASSERTED, AND WHY THIS SHAPE
 * ---------------------------------------------------------------------------
 * Three relative invariants, none of which hard-codes a pixel size:
 *
 *   1. **The column fills `<main>` exactly.** Not "is no taller than" — exactly. Too tall
 *      is the original defect; too short is the same empty band arriving from the other
 *      direction.
 *   2. **The column's children tile it with no gap and no overlap.** This is the band
 *      assertion stated properly: each child begins where the previous one ended, and the
 *      last ends at the column's floor. It therefore detects a gap ANYWHERE in the stack
 *      rather than only between the two elements the original bug happened to separate.
 *   3. **The document does not scroll.** `<main>` owns `overflowY: auto`, so a
 *      document-level scrollbar means something escaped it.
 *
 * MEASURED STRUCTURE, so the next reader does not have to rediscover it. At 1280x720 the
 * column's children are, in order:
 *
 *     toolbar        56 -> 297   flex: 0 1 auto
 *     builder-shell  297 -> 659  flex: 1 1 0%     (the palette/canvas/inspector grid)
 *     status-strip   659 -> 720  flex: 0 1 auto
 *
 * `builder-shell` is NOT expected to reach the column's floor: `status-strip` is 61px of
 * legitimate chrome below it. An assertion that the shell fills the column is wrong, and
 * failed exactly that way when this file was first written. The flexible child is
 * identified by its computed `flex-grow`, not by position or test id, so adding another
 * fixed strip above or below does not need this file edited.
 *
 * WHY NO ZOOM CASE, STATED SO NOBODY ASSUMES IT IS COVERED
 * ---------------------------------------------------------------------------
 * The zoom dependence was the same bug from another angle: zoom changes the CSS-pixel
 * height of fixed chrome but not the definition of `vh`. Playwright has no browser-zoom
 * control — `deviceScaleFactor` changes device pixel ratio, which is not zoom and would
 * not reproduce it. These assertions are relative, so a `vh` regression fails them at one
 * viewport or another regardless of zoom. Zoom itself is not measured here.
 *
 * NO BACKEND IS REQUIRED OR CONTACTED
 * ---------------------------------------------------------------------------
 * Same admission route `accountMenuAnchoring.spec.ts` documents: `AuthGuard` calls
 * `getEffectiveToken()`, which discards a stored token only when `isDemonstrablyExpiredJwt`
 * PROVES it expired, and that helper returns `false` for any string that is not three
 * dot-separated segments. An opaque string is therefore admitted into the shell — no real
 * credential, no real session, and no network call authorised by it. These specs assert
 * chrome geometry, never data.
 */
import { test, expect, type Page } from '@playwright/test';

/*
 * A cold Vite dev server compiles the route graph on the first request and the builder is
 * the largest module in the app, so the first navigation here is slow in a way later ones
 * are not. Serial, with a suite timeout that absorbs that compile without masking a hang —
 * the same reasoning `accountMenuAnchoring.spec.ts` records.
 */
test.describe.configure({ mode: 'serial', timeout: 150_000 });

const BUILDER_SHELL = '[data-testid="builder-shell"]';

/**
 * 1280x720 is the size the T03 decision names. The other two bracket it so a regression
 * that only shows on a short or a tall viewport cannot hide behind one measurement.
 */
const VIEWPORTS = [
  { name: '1280x720', width: 1280, height: 720 },
  { name: '1440x900', width: 1440, height: 900 },
  { name: '1920x1080', width: 1920, height: 1080 },
] as const;

/**
 * Sub-pixel layout is normal: a `1fr` grid row on an odd viewport height resolves to a
 * fractional pixel and `getBoundingClientRect()` reports it faithfully. 1px absorbs that
 * and nothing else — the original overflow was the full height of the TopBar, tens of
 * pixels.
 */
const SUBPIXEL_PX = 1;

type Geometry = {
  main: { top: number; bottom: number; height: number; overflowY: string };
  column: { top: number; bottom: number; height: number };
  children: Array<{ testid: string | null; top: number; bottom: number; height: number; grow: number }>;
  canvas: { height: number } | null;
  doc: { scrollHeight: number; clientHeight: number };
};

async function measureBuilder(page: Page, width: number, height: number): Promise<Geometry> {
  await page.setViewportSize({ width, height });
  await page.addInitScript(() => {
    sessionStorage.setItem('token', 'e2e-layout-probe-token');
    localStorage.removeItem('vyomquant-sidebar-collapsed');
  });
  // `domcontentloaded`, not `load`: `index.html` pulls webfonts from fonts.googleapis.com,
  // and `load` waits for every subresource — so on a host where that CDN is slow the
  // navigation never settles and the spec fails for a reason unrelated to layout. The
  // selector wait below is the real precondition.
  await page.goto('/app/builder', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector(BUILDER_SHELL, { state: 'visible', timeout: 120_000 });

  return page.evaluate(() => {
    const shell = document.querySelector('[data-testid="builder-shell"]');
    const main = document.querySelector('main#main-content');
    if (!shell || !main) throw new Error('builder shell or main landmark not found');

    // The column is whichever ancestor of the shell is a direct child of <main>. Derived
    // rather than selected, because it carries no test id and `main > div:first-child`
    // would silently follow a wrapper being introduced.
    let column: Element = shell;
    while (column.parentElement && column.parentElement !== main) {
      column = column.parentElement;
    }
    if (column.parentElement !== main) throw new Error('builder column is not a child of <main>');

    const rect = (el: Element) => {
      const r = el.getBoundingClientRect();
      return { top: r.top, bottom: r.bottom, height: r.height };
    };

    const scroller = document.scrollingElement ?? document.documentElement;
    const canvas = document.querySelector('[data-testid="canvas"]');

    return {
      main: { ...rect(main), overflowY: getComputedStyle(main).overflowY },
      column: rect(column),
      children: Array.from(column.children).map((c) => ({
        testid: c.getAttribute('data-testid'),
        ...rect(c),
        grow: parseFloat(getComputedStyle(c).flexGrow || '0'),
      })),
      canvas: canvas ? { height: canvas.getBoundingClientRect().height } : null,
      doc: { scrollHeight: scroller.scrollHeight, clientHeight: scroller.clientHeight },
    };
  });
}

/** Names a child for a failure message: its test id, or its index and size. */
const label = (c: { testid: string | null; height: number }, i: number) =>
  c.testid ? `"${c.testid}"` : `child ${i} (${Math.round(c.height)}px tall)`;

test.describe('Strategy Builder fits the shell it renders in (@layout)', () => {
  for (const vp of VIEWPORTS) {
    test(`${vp.name}: the builder column fills <main> exactly`, async ({ page }) => {
      const g = await measureBuilder(page, vp.width, vp.height);

      // `<main>` is the element that owns the page's vertical space, and it is the one the
      // builder was measuring past when it used `100vh`.
      expect(g.main.overflowY, '<main> should own the scroll').toBe('auto');

      expect(
        Math.abs(g.column.top - g.main.top),
        `the builder column starts ${(g.column.top - g.main.top).toFixed(1)}px from <main>'s `
          + `top at ${vp.name}`,
      ).toBeLessThanOrEqual(SUBPIXEL_PX);

      // THE ASSERTION. With `height: 100vh` the column is taller than <main> by the height
      // of the chrome above it, which is what rendered as the empty band and the scroll.
      expect(
        Math.abs(g.column.bottom - g.main.bottom),
        `the builder column's bottom (${g.column.bottom.toFixed(1)}) does not match <main>'s `
          + `(${g.main.bottom.toFixed(1)}) at ${vp.name}: off by `
          + `${(g.column.bottom - g.main.bottom).toFixed(1)}px. Positive means it overflows `
          + `its container, which is LAYOUT-01; negative means an empty band below it.`,
      ).toBeLessThanOrEqual(SUBPIXEL_PX);
    });

    test(`${vp.name}: the column's children tile it with no empty band`, async ({ page }) => {
      const g = await measureBuilder(page, vp.width, vp.height);

      expect(g.children.length, 'the builder column should have children').toBeGreaterThan(0);

      // Exactly one child absorbs the slack. If none does, nothing fills the column and a
      // band is inevitable; if several do, this assertion is not the right shape any more
      // and should be rewritten rather than relaxed.
      const flexible = g.children.filter((c) => c.grow > 0);
      expect(
        flexible.length,
        `expected exactly one flex-grow child in the builder column at ${vp.name}, found `
          + `${flexible.length}: ${g.children.map((c, i) => `${label(c, i)} grow=${c.grow}`).join(', ')}`,
      ).toBe(1);

      // Each child begins where the previous ended. A gap here IS the reported defect; an
      // overlap is the same arithmetic going the other way.
      for (let i = 1; i < g.children.length; i += 1) {
        const prev = g.children[i - 1];
        const curr = g.children[i];
        expect(
          Math.abs(curr.top - prev.bottom),
          `${label(curr, i)} starts ${(curr.top - prev.bottom).toFixed(1)}px after `
            + `${label(prev, i - 1)} ends at ${vp.name}. A positive number is an empty band `
            + `between them, which is the defect LAYOUT-01 reported; a negative one is an `
            + `overlap.`,
        ).toBeLessThanOrEqual(SUBPIXEL_PX);
      }

      // And the stack reaches the floor, so there is no band below the last child either.
      const last = g.children[g.children.length - 1];
      expect(
        Math.abs(last.bottom - g.column.bottom),
        `${label(last, g.children.length - 1)} ends `
          + `${(g.column.bottom - last.bottom).toFixed(1)}px above the column's floor at `
          + `${vp.name}, which is an empty band below it.`,
      ).toBeLessThanOrEqual(SUBPIXEL_PX);
    });

    test(`${vp.name}: the shell scrolls inside <main>, not the document`, async ({ page }) => {
      const g = await measureBuilder(page, vp.width, vp.height);

      expect(
        g.doc.scrollHeight,
        `the document scrolls at ${vp.name} (scrollHeight ${g.doc.scrollHeight} > clientHeight `
          + `${g.doc.clientHeight}). A page inside the shell must scroll within <main>, which `
          + `owns the overflow.`,
      ).toBeLessThanOrEqual(g.doc.clientHeight + SUBPIXEL_PX);

      // Guards every assertion in this file against passing vacuously: a collapsed canvas
      // satisfies all of them trivially, and a zero height is exactly what jsdom reports —
      // which is the whole reason this file runs in a real browser.
      expect(g.canvas, 'the canvas should be in the DOM').not.toBeNull();
      expect(
        g.canvas!.height,
        `the canvas has height ${g.canvas!.height} at ${vp.name}; the container assertions `
          + `would pass vacuously against a collapsed canvas.`,
      ).toBeGreaterThan(100);
    });
  }
});
