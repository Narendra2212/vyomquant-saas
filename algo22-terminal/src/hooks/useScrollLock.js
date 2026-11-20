/**
 * ═══════════════════════════════════════════════════════════════════════════
 * useScrollLock — the page behind an overlay does not scroll (CHART-MODAL-01)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Task 06, second half. `components/ExpandableChart` opened a dialog over the Backtester
 * and left the document scrollable underneath it: a wheel gesture aimed at the enlarged
 * chart scrolled the results page behind the backdrop, so closing the dialog returned the
 * trader to a different scroll position than the one they left — and on a touch device the
 * dialog itself drifted off screen while the page moved under it.
 *
 * WHAT THIS HOOK OWNS
 * -------------------
 * One declaration — `overflow: hidden` on `<body>` — plus the two things that make it safe
 * to say more than once:
 *
 *   1. **A reference count.** Taken on the 0 → 1 transition and given back on the 1 → 0
 *      transition, and nothing in between touches the DOM.
 *   2. **A restore point.** The *inline* values that were on `<body>` when the first lock
 *      engaged, put back exactly — including removing the property when there was no inline
 *      value at all.
 *
 * It owns nothing else. Focus belongs to `hooks/useFocusTrap`, mutual exclusion to
 * `components/ds/overlayRegistry`, and rendering to the overlay.
 *
 * ---------------------------------------------------------------------------
 * WHY A COUNT, WHEN `overlayRegistry` ALREADY ALLOWS ONLY ONE OVERLAY
 * ---------------------------------------------------------------------------
 * Because the registry arbitrates *registered* overlays and a scroll lock is not registered.
 * `components/landing/Navbar.jsx` already locks `<body>` for its mobile menu, by hand, with
 * the same save-and-restore idiom and no registry claim at all. Two independent locks is
 * therefore not a hypothetical: the naive version of this hook —
 *
 *     const previous = document.body.style.overflow;   // reads 'hidden', not ''
 *     document.body.style.overflow = 'hidden';
 *     return () => { document.body.style.overflow = previous; };
 *
 * — is correct alone and wrong in company. Whichever overlay closes *second* restores the
 * value it read on the way in, which is the first overlay's `hidden`, and the page stays
 * frozen with no overlay on screen and nothing visible to blame. Counting makes the order
 * of closing irrelevant, which is the property that actually has to hold: a trader can
 * close two overlays in either order, and React can unmount them in either order.
 *
 * The count lives at module scope for `overlayRegistry`'s reason — one place that every
 * caller must pass through beats a context provider a future page can forget to mount.
 *
 * ---------------------------------------------------------------------------
 * WHY `<body>` AND NOT `<html>`
 * ---------------------------------------------------------------------------
 * CSS propagates the viewport's overflow from the root element, and when the root's
 * overflow is `visible` — which it is here, since `src/index.css` sets only
 * `scroll-behavior` on `html` — the viewport takes `<body>`'s instead. So `<body>` is
 * enough, and it is what `Navbar` already does; two overlays disagreeing about *which*
 * element the lock lives on would be a third way for the restore to go wrong.
 *
 * ---------------------------------------------------------------------------
 * WHY THE SCROLLBAR'S WIDTH IS PAID BACK AS PADDING
 * ---------------------------------------------------------------------------
 * Hiding the document's overflow removes the classic scrollbar, and the content underneath
 * widens by exactly its width — every panel, table and chart on the page behind the
 * backdrop reflows the moment the dialog opens, and reflows back when it closes. On the
 * Backtester that is the full results page jumping sideways behind a dialog the trader
 * opened to read one chart more carefully.
 *
 * {@link scrollbarGap} measures the difference between the window and the document element
 * *before* the overflow changes — after it, the gap reads zero and there is nothing left to
 * compensate — and the first lock adds it to `<body>`'s padding. The dialog itself is
 * `position: fixed`, so its containing block is the viewport and body padding does not move
 * it. Overlay scrollbars (macOS, and Windows 11's auto-hiding ones) measure zero, so
 * nothing is added where nothing was taken away.
 *
 * `scrollbar-gutter: stable` on `html` would be the one-line CSS alternative and is the
 * better long-term answer, but it is a global change to the app's chrome — it reserves the
 * gutter on every page, at every viewport, forever — and that is not a decision a chart
 * modal gets to make. This stays local to the lock and leaves the stylesheet alone.
 *
 * @module hooks/useScrollLock
 */

import { useEffect } from 'react';

/**
 * How many consumers hold the lock.
 *
 * @type {number}
 */
let depth = 0;

/**
 * What to put back when the last consumer lets go, or `null` while unlocked.
 *
 * The element is remembered alongside its values rather than re-resolved from `document` on
 * release: whatever was mutated is what gets restored, which is the whole guarantee.
 *
 * @type {{body: HTMLElement, overflow: string, paddingRight: string}|null}
 */
let restorePoint = null;

/** The `<body>` of the current document, or `null` where there is no document. */
function resolveBody() {
  if (typeof document === 'undefined') return null;
  return document.body ?? null;
}

/**
 * The width the classic scrollbar is occupying right now, in CSS pixels.
 *
 * Zero for overlay scrollbars, for a page shorter than the viewport, and under jsdom —
 * which reports `innerWidth` and `clientWidth` as the same number, so a test environment
 * adds no padding unless a test deliberately simulates a scrollbar.
 *
 * @param {HTMLElement} body
 * @returns {number}
 */
export function scrollbarGap(body) {
  const view = body?.ownerDocument?.defaultView;
  if (!view) return 0;
  const innerWidth = typeof view.innerWidth === 'number' ? view.innerWidth : 0;
  const clientWidth = body.ownerDocument.documentElement?.clientWidth ?? 0;
  if (innerWidth <= 0 || clientWidth <= 0) return 0;
  return Math.max(0, innerWidth - clientWidth);
}

/** Freeze the document, remembering what it looked like first. */
function engage(body) {
  const previous = {
    body,
    // The INLINE values, not the computed ones. A computed `'visible'` written back as an
    // inline style is not a restore — it is a new declaration that outranks the stylesheet
    // and would quietly win every future cascade on this element.
    overflow: body.style.overflow,
    paddingRight: body.style.paddingRight,
  };

  const gap = scrollbarGap(body);
  if (gap > 0) {
    const view = body.ownerDocument.defaultView;
    const computed = typeof view.getComputedStyle === 'function'
      ? view.getComputedStyle(body).paddingRight
      : '';
    const existing = Number.parseFloat(computed);
    // Added to whatever the stylesheet already asks for, not substituted for it. The app's
    // reset zeroes body padding today; a layout that changes that must not lose it here.
    body.style.paddingRight = `${(Number.isFinite(existing) ? existing : 0) + gap}px`;
  }

  body.style.overflow = 'hidden';
  restorePoint = previous;
}

/** Put the document back exactly as it was. */
function restore() {
  const point = restorePoint;
  restorePoint = null;
  if (point === null) return;

  const { body, overflow, paddingRight } = point;
  // An empty string means there was no inline declaration at all, so the declaration is
  // REMOVED rather than set to anything. `style.overflow = ''` would have the same effect
  // and `removeProperty` says which of the two cases this is; what neither must do is write
  // back a computed `visible`, which would be a brand-new inline declaration outranking the
  // stylesheet for the rest of the page's life. (The `style` attribute itself survives, as
  // an empty one — the DOM keeps it once it has been touched.)
  if (overflow === '') body.style.removeProperty('overflow');
  else body.style.overflow = overflow;
  if (paddingRight === '') body.style.removeProperty('padding-right');
  else body.style.paddingRight = paddingRight;
}

/**
 * Claim the lock. The first claim freezes the document; later ones only count.
 *
 * @returns {number} the depth after this claim.
 */
export function acquireScrollLock() {
  const body = resolveBody();
  if (body === null) return depth;
  depth += 1;
  if (depth === 1) engage(body);
  return depth;
}

/**
 * Give the lock back. The last release restores the document; earlier ones only count.
 *
 * Safe to call when nothing is held — a release with no matching claim is a no-op rather
 * than a negative depth, so one component unmounting twice (React 18 StrictMode mounts
 * effects, tears them down and mounts them again) cannot make the count drift below zero
 * and strand a later lock.
 *
 * @returns {number} the depth after this release.
 */
export function releaseScrollLock() {
  if (depth === 0) return 0;
  depth -= 1;
  if (depth === 0) restore();
  return depth;
}

/**
 * How many consumers currently hold the lock.
 *
 * Exported so a test can assert the count comes back to zero — a lock that is restored but
 * still counted would refuse to engage the next time, and the DOM alone cannot show that.
 *
 * @returns {number}
 */
export function scrollLockDepth() {
  return depth;
}

/**
 * Drop every claim and restore the document.
 *
 * For tests, which need a clean module between cases, exactly as
 * `ds/overlayRegistry.resetOverlayRegistry` does. Application code releases its own claim
 * with {@link releaseScrollLock}: an unconditional reset from a component would unfreeze
 * the page under an overlay somebody else still has open.
 *
 * @returns {void}
 */
export function resetScrollLock() {
  depth = 0;
  restore();
}

/**
 * Hold the scroll lock for as long as `locked` is true.
 *
 * The claim is taken in an effect and released in its cleanup, so it follows the component:
 * unmounting while locked releases, which is the case a handler on the close button alone
 * would miss (a route change, an error boundary, or a parent that stops rendering the
 * overlay all unmount it without anyone clicking anything).
 *
 * @param {boolean} locked Whether this consumer wants the document frozen. Anything other
 *   than `true` is treated as unlocked, so an `undefined` from a half-written caller cannot
 *   freeze the page.
 * @returns {void}
 */
export function useScrollLock(locked) {
  useEffect(() => {
    if (locked !== true) return undefined;
    acquireScrollLock();
    return () => {
      releaseScrollLock();
    };
  }, [locked]);
}

export default useScrollLock;
