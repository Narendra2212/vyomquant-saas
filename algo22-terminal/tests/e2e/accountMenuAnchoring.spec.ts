/**
 * Browser-level regression coverage for `shell/AccountMenu`'s popover anchoring.
 *
 * WHY THIS EXISTS, AND WHY IT IS NOT A UNIT TEST
 * ---------------------------------------------
 * The panel is portalled to `document.body` and positioned from a measured
 * `getBoundingClientRect()` of its trigger. Under jsdom every rect is zeroes, so a
 * positional assertion there passes whether or not the measurement ran — it cannot
 * distinguish a working anchor from a broken one. Everything in this file therefore reads
 * real geometry through `boundingBox()` in a real browser.
 *
 * WHAT THE IMPLEMENTATION PROMISES (src/components/shell/AccountMenu.jsx)
 * ---------------------------------------------------------------------
 * `popoverPosition(rect, viewport)` is pure and reads exactly four inputs — `rect.left`,
 * `rect.top`, `viewport.width`, `viewport.height` — and returns `{ left, bottom, maxHeight }`
 * which are applied as inline `position: fixed` styles:
 *
 *     left   = clamp(VIEWPORT_GUTTER, rect.left, viewportWidth - PANEL_WIDTH - VIEWPORT_GUTTER)
 *     bottom = max(VIEWPORT_GUTTER, viewportHeight - rect.top + TRIGGER_GAP)   // room above
 *     maxHeight = rect.top - VIEWPORT_GUTTER - TRIGGER_GAP
 *
 * …falling back to a viewport-pinned placement when the room above the trigger is less
 * than MIN_PANEL_HEIGHT.
 *
 * NOTE ON `rect.width`: it is NOT an input. That single fact is what spec 3 below is about.
 */

import { test, expect, type Page } from '@playwright/test';

/*
 * A cold Vite dev server compiles the route graph on the first request, and the shared
 * `playwright.config.ts` leaves `fullyParallel: true`, so four specs arriving at once on a
 * cold server each wait on that single compile. 90s absorbs it without masking a real hang
 * — the per-action waits inside each spec stay at Playwright's defaults.
 */
test.describe.configure({ mode: 'serial', timeout: 90_000 });

/** Mirrors the module constants in `shell/AccountMenu.jsx`. Kept in sync by spec 1. */
const PANEL_WIDTH_PX = 232;
const VIEWPORT_GUTTER_PX = 8;
const TRIGGER_GAP_PX = 6;

/** Mirrors `SIDEBAR_WIDTH_PX` in `shell/ResponsiveGate.jsx`. */
const SIDEBAR_EXPANDED_PX = 216;
const SIDEBAR_RAIL_PX = 56;

const SIDEBAR = '[data-shell="sidebar"]';
const ACCOUNT_TRIGGER = '[data-shell="sidebar-account"] button';
const PANEL = '[data-shell="account-menu"]';
const SCRIM = '[data-shell="account-menu-scrim"]';
const COLLAPSE_TOGGLE = '[data-shell="sidebar-toggle"] button';

/**
 * Puts the browser inside the authenticated shell.
 *
 * `AuthGuard` calls `getEffectiveToken()`, which reads `sessionStorage.token` and discards
 * it only when `isDemonstrablyExpiredJwt` proves it expired. That helper returns `false`
 * for any string that is not three dot-separated segments, so an opaque token is admitted
 * — no real credential, no real session, and no network call is authorised by it.
 *
 * The sidebar collapse preference lives in `localStorage` under
 * `vyomquant-sidebar-collapsed`; it is cleared so every spec starts expanded regardless of
 * what a previous spec left behind.
 */
async function openShell(page: Page, width: number, height: number) {
  await page.setViewportSize({ width, height });
  await page.addInitScript(() => {
    sessionStorage.setItem('token', 'e2e-anchoring-probe-token');
    localStorage.removeItem('vyomquant-sidebar-collapsed');
  });
  // `domcontentloaded`, NOT the default `load`. `index.html` pulls Inter and JetBrains Mono
  // from `fonts.googleapis.com`, and `load` waits for every subresource — so on a host
  // where that CDN is slow or unreachable the navigation never settles and the spec fails
  // for a reason that has nothing to do with popover geometry. Two of these four specs
  // failed exactly that way before this change. Readiness is established by the selector
  // wait below, which is the real precondition.
  await page.goto('/app/trades', { waitUntil: 'domcontentloaded' });
  // The shell, not the page: these specs assert chrome geometry and must not wait on a
  // data read that has no backend in this environment.
  await page.waitForSelector(SIDEBAR, { state: 'visible', timeout: 60_000 });
}

async function box(page: Page, selector: string) {
  const handle = page.locator(selector);
  await expect(handle).toBeVisible();
  const b = await handle.boundingBox();
  if (b === null) throw new Error(`no bounding box for ${selector}`);
  return b;
}

/** The `left` the implementation should have chosen for a trigger at `triggerLeft`. */
function expectedLeft(triggerLeft: number, viewportWidth: number) {
  const rightmost = viewportWidth - PANEL_WIDTH_PX - VIEWPORT_GUTTER_PX;
  return Math.max(VIEWPORT_GUTTER_PX, Math.min(triggerLeft, rightmost));
}

test.describe('AccountMenu popover anchoring (real browser geometry)', () => {
  // ───────────────────────────────────────────────────────────────────────────
  // 1. The panel is anchored to its trigger, not placed at a fixed point.
  // ───────────────────────────────────────────────────────────────────────────
  test('anchors to the trigger: left tracks the trigger, and it opens upward', async ({ page }) => {
    await openShell(page, 1440, 900);

    const trigger = await box(page, ACCOUNT_TRIGGER);
    await page.locator(ACCOUNT_TRIGGER).click();
    const panel = await box(page, PANEL);

    // Width is the declared constant — this is what keeps PANEL_WIDTH_PX above honest.
    expect(Math.round(panel.width)).toBe(PANEL_WIDTH_PX);

    // THE ANCHOR. A panel at a hardcoded position would fail this.
    expect(Math.round(panel.x)).toBe(Math.round(expectedLeft(trigger.x, 1440)));

    // It opens UPWARD, with the declared gap between the panel's bottom edge and the
    // trigger's top edge. `bottom` in the implementation is measured from the viewport
    // floor, so this is the assertion that proves the arithmetic landed.
    const panelBottomEdge = panel.y + panel.height;
    expect(panelBottomEdge).toBeLessThanOrEqual(trigger.y);
    expect(Math.abs(trigger.y - panelBottomEdge - TRIGGER_GAP_PX)).toBeLessThanOrEqual(1);

    // And it stays inside the viewport on every side.
    expect(panel.x).toBeGreaterThanOrEqual(VIEWPORT_GUTTER_PX - 1);
    expect(panel.y).toBeGreaterThanOrEqual(VIEWPORT_GUTTER_PX - 1);
    expect(panel.x + panel.width).toBeLessThanOrEqual(1440 - VIEWPORT_GUTTER_PX + 1);
  });

  // ───────────────────────────────────────────────────────────────────────────
  // 2. THE LOAD-BEARING SPEC: it asserts `maxHeight`, and that choice is the whole point.
  //
  // An earlier draft of this spec shrank the window and asserted that the gap between the
  // panel's bottom edge and the trigger's top edge was preserved. A mutation run — the
  // `resize` listener deleted from the measuring effect — showed that assertion PASSING,
  // so it guarded nothing. The arithmetic explains why:
  //
  //     bottom = viewportHeight - rect.top + TRIGGER_GAP
  //
  // and the trigger is the sidebar's footer, pinned to the bottom of a full-height column,
  // so `rect.top` is always `viewportHeight - 56`. Substituting, `bottom` is a CONSTANT 62
  // at every viewport height. `left` is likewise invariant: the sidebar's left edge is the
  // viewport's, so `rect.left` is 0 and the clamp always yields the 8px gutter.
  //
  // `maxHeight = rect.top - VIEWPORT_GUTTER - TRIGGER_GAP = viewportHeight - 70` is the ONLY
  // output that varies with the viewport. So it is the only thing a re-measurement test can
  // legitimately assert, and a stale one is a real visual defect: the panel keeps the taller
  // clamp, renders past its allowance, and runs off the TOP of the window.
  //
  // Mutation-verified: with `window.addEventListener('resize', sync)` removed, this spec
  // fails on the `panel.y` assertion with a negative y.
  // ───────────────────────────────────────────────────────────────────────────
  test('re-clamps its height when the viewport shrinks while the panel is open', async ({ page }) => {
    await openShell(page, 1440, 960);

    await page.locator(ACCOUNT_TRIGGER).click();
    await expect(page.locator(PANEL)).toBeVisible();

    const tall = await box(page, PANEL);
    const SHRUNK_HEIGHT = 300;
    // `maxHeight` at the shrunk viewport. The panel must be taller than this at 960, or the
    // clamp would not bind and the spec could not detect a stale measurement — so this is a
    // PRECONDITION, asserted rather than assumed.
    const clampAtShrunk = SHRUNK_HEIGHT - VIEWPORT_GUTTER_PX - TRIGGER_GAP_PX - 56;
    expect(
      tall.height,
      `panel is ${tall.height}px tall; it must exceed the ${clampAtShrunk}px clamp for this spec to be meaningful`,
    ).toBeGreaterThan(clampAtShrunk);

    await page.setViewportSize({ width: 1440, height: SHRUNK_HEIGHT });
    await expect(page.locator(PANEL)).toBeVisible();

    const shrunk = await box(page, PANEL);

    // THE ASSERTION THAT CATCHES A STALE MEASUREMENT. With the effect not re-running, the
    // panel keeps its 890px allowance, renders its full natural height, and its top edge
    // goes negative — off the top of the window.
    expect(shrunk.y).toBeGreaterThanOrEqual(VIEWPORT_GUTTER_PX - 1);
    expect(shrunk.height).toBeLessThanOrEqual(clampAtShrunk + 1);

    // Fully on screen vertically, and still anchored horizontally.
    expect(shrunk.y + shrunk.height).toBeLessThanOrEqual(SHRUNK_HEIGHT);
    expect(Math.round(shrunk.x)).toBe(Math.round(expectedLeft(shrunk.x, 1440)));
  });

  // ───────────────────────────────────────────────────────────────────────────
  // 2b. The invariants spec 2's comment derives, asserted directly so the derivation is
  // not something a future reader has to take on trust.
  // ───────────────────────────────────────────────────────────────────────────
  test('keeps the trigger gap and the left gutter invariant across viewport heights', async ({ page }) => {
    await openShell(page, 1440, 960);
    await page.locator(ACCOUNT_TRIGGER).click();
    await expect(page.locator(PANEL)).toBeVisible();

    for (const height of [960, 820, 700]) {
      await page.setViewportSize({ width: 1440, height });
      await expect(page.locator(PANEL)).toBeVisible();

      const trigger = await box(page, ACCOUNT_TRIGGER);
      const panel = await box(page, PANEL);

      // The trigger tracks the viewport floor…
      expect(Math.round(trigger.y + trigger.height)).toBe(height);
      // …and the panel keeps the declared gap above it at every height, which is why
      // `bottom` alone cannot detect a stale measurement.
      expect(Math.abs(trigger.y - (panel.y + panel.height) - TRIGGER_GAP_PX)).toBeLessThanOrEqual(1);
      expect(Math.round(panel.x)).toBe(VIEWPORT_GUTTER_PX);
    }
  });

  // ───────────────────────────────────────────────────────────────────────────
  // 3. The sidebar-collapse case, MEASURED rather than assumed.
  //
  // A previous report claimed that collapsing the sidebar moves this trigger from 216px
  // to 56px and so requires re-measurement. This spec exists because that claim is
  // testable, and the two assertions below are what it actually resolves to:
  //
  //   * the sidebar's WIDTH changes (216 -> 56);
  //   * the trigger's `left` and `top` — the only rect fields `popoverPosition` reads —
  //     do NOT change, because the sidebar occupies grid column 1 and its left edge is
  //     the viewport's, at every width.
  //
  // So the collapse cannot move this popover, and the `rail` entry in the measuring
  // effect's dependency list is defensive rather than load-bearing. Recorded here so the
  // next reader gets the measurement instead of the claim.
  // ───────────────────────────────────────────────────────────────────────────
  test('collapsing the sidebar changes its width but not the trigger anchor point', async ({ page }) => {
    await openShell(page, 1440, 900);

    const sidebarExpanded = await box(page, SIDEBAR);
    const triggerExpanded = await box(page, ACCOUNT_TRIGGER);
    expect(Math.round(sidebarExpanded.width)).toBe(SIDEBAR_EXPANDED_PX);

    await page.locator(COLLAPSE_TOGGLE).click();
    await expect(page.locator(SIDEBAR)).toHaveAttribute('data-sidebar-mode', 'rail');

    const sidebarRail = await box(page, SIDEBAR);
    const triggerRail = await box(page, ACCOUNT_TRIGGER);

    // The width really does collapse.
    expect(Math.round(sidebarRail.width)).toBe(SIDEBAR_RAIL_PX);

    // The anchor point does not move on either axis.
    expect(Math.round(triggerRail.x)).toBe(Math.round(triggerExpanded.x));
    expect(Math.round(triggerRail.y)).toBe(Math.round(triggerExpanded.y));

    // And the popover anchors identically in the collapsed state.
    await page.locator(ACCOUNT_TRIGGER).click();
    const panel = await box(page, PANEL);
    expect(Math.round(panel.x)).toBe(Math.round(expectedLeft(triggerRail.x, 1440)));
  });

  // ───────────────────────────────────────────────────────────────────────────
  // 4. Why case 3 cannot be reached by a user at all: the scrim is in front.
  //
  // The portal layer is `position: fixed; inset: 0; z-index: var(--z-dropdown)` and the
  // scrim fills it, so a pointer press aimed at the sidebar's collapse toggle lands on
  // the scrim and dismisses the panel. The toggle never receives it.
  //
  // This is a real regression guard, not a curiosity: if the scrim is later removed, or
  // the sidebar is given a z-index above the dropdown layer, "collapse with the panel
  // open" becomes reachable and this spec fails — which is the signal to check whether
  // the popover still anchors correctly in that newly reachable state.
  // ───────────────────────────────────────────────────────────────────────────
  test('a click aimed at the collapse toggle while the panel is open hits the scrim and dismisses', async ({ page }) => {
    await openShell(page, 1440, 900);

    const toggle = await box(page, COLLAPSE_TOGGLE);
    await page.locator(ACCOUNT_TRIGGER).click();
    await expect(page.locator(PANEL)).toBeVisible();
    await expect(page.locator(SCRIM)).toBeVisible();

    // The scrim covers the toggle's centre: whatever is topmost at that point is what a
    // real click would hit.
    const centre = { x: toggle.x + toggle.width / 2, y: toggle.y + toggle.height / 2 };
    const topmost = await page.evaluate(
      (pt) => {
        const el = document.elementFromPoint(pt.x, pt.y);
        return el === null ? null : (el.getAttribute('data-shell') ?? el.tagName);
      },
      centre,
    );
    expect(topmost).toBe('account-menu-scrim');

    await page.mouse.click(centre.x, centre.y);

    // The panel is dismissed and the sidebar did NOT collapse — the click was consumed.
    await expect(page.locator(PANEL)).toHaveCount(0);
    await expect(page.locator(SIDEBAR)).toHaveAttribute('data-sidebar-mode', 'expanded');
  });
});
