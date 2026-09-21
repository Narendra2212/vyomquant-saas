/**
 * ═══════════════════════════════════════════════════════════════════════════
 * useFocusTrap — keyboard focus cannot leave an open overlay (design §11.7)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.20. Requirements 18.1, 18.2, 18.3. Property P35.
 *
 * WHAT THIS HOOK OWNS
 * -------------------
 * Four things, for exactly as long as an overlay is open:
 *
 *   1. **Initial focus.** On the element the caller nominates — for `ConfirmDialog` that is
 *      the *cancel* action, never confirm (see below).
 *   2. **The Tab cycle.** Tab and Shift+Tab move between the container's focusable
 *      descendants and wrap. The key is always consumed, so the browser never gets to move
 *      focus itself. That is what makes P35's claim total rather than probable.
 *   3. **Escape.** Delegated to `onEscape`; the hook decides nothing about what closing
 *      means.
 *   4. **The rest of the app.** Every `document.body` child that does not contain the
 *      container is marked `aria-hidden` while the overlay is open, and put back exactly as
 *      it was afterwards.
 *
 * And on close: **focus returns to the opener**, so a keyboard user is put back where they
 * were rather than at the top of the document.
 *
 * It renders nothing and owns no styling. It does not manage the overlay registry either —
 * `components/ds/overlayRegistry.js` owns that.
 *
 * WHY INITIAL FOCUS IS ON CANCEL
 * ------------------------------
 * The caller passes `initialFocusRef`, and both overlay components point it at the
 * least-destructive control. On a live-deploy confirmation the confirm button places real
 * orders with real funds (Requirement 8.2); a trader who opens the dialog and hits Space or
 * Enter out of habit must land on "no". `window.confirm` cannot express this preference at
 * all, which is one of the reasons §1.8's six native dialogs have to go.
 *
 * THE FOUR AWKWARD CASES
 * ----------------------
 * P35 generates Tab/Shift+Tab sequences of arbitrary length and asserts the active element
 * is *always* a descendant. That makes the degenerate cases load-bearing rather than
 * theoretical:
 *
 *   * **Zero focusables.** A dialog whose body is text and whose buttons are all disabled
 *     (`busy`) has nothing to focus. The container itself carries `tabIndex={-1}`, so focus
 *     goes there and Tab keeps it there. Without this, focus sits on `<body>` and Tab walks
 *     out into the page behind the overlay.
 *   * **One focusable.** The modular cycle is `(i ± 1) % n`, which for `n === 1` re-focuses
 *     the same element. No special case is needed, and the key is still consumed so the
 *     browser cannot advance past it.
 *   * **An element becoming disabled while open.** Pressing confirm sets `busy`, which
 *     disables both buttons; a browser blurs a disabled element and focus drops to `<body>`.
 *     The candidate list is therefore recomputed on **every** keystroke rather than
 *     collected once, and an `activeElement` that is not in the current list is treated as
 *     "outside", so the next Tab re-enters at the first (or last) focusable.
 *   * **Content changing while open.** §8.3's deploy flow swaps the dialog's whole body
 *     between Configure, Review and AckLive steps, and the acknowledgement checkbox appears
 *     mid-flow. Same mechanism: nothing is cached, so a control that did not exist a moment
 *     ago is in the cycle as soon as it is in the DOM.
 *
 * WHY THE KEY LISTENER IS ON `document`, IN CAPTURE
 * ------------------------------------------------
 * A listener on the container only sees keys while focus is inside it — precisely not the
 * case in the third scenario above, where focus has dropped to `<body>`. Listening on
 * `document` in the capture phase means the trap sees every Tab regardless of where focus
 * currently sits, and sees it before any page-level handler. Only one overlay can be open
 * at a time (Requirement 17.3, enforced by the registry), so there is never a second trap
 * to contend with.
 *
 * A `focusin` listener is the second half of the same guarantee: it catches focus arriving
 * outside the container by any route the keyboard handler cannot see — a programmatic
 * `.focus()` from a page effect, a mouse click on the page behind, a browser restoring
 * focus after an element vanished — and pulls it back.
 *
 * WHY NOT A LIBRARY
 * -----------------
 * Task 6.20 forbids a new dependency, and rightly: `focus-trap`/`react-focus-lock` are
 * ~10–15 kB for behaviour this file states in one screen, and both need configuring around
 * the same four cases anyway. The one thing a library would buy is a layout-based
 * visibility test, and that is exactly the part that cannot be shared with jsdom — see
 * {@link isVisible}.
 */

import { useCallback, useEffect, useRef } from 'react';

/**
 * Everything that can hold keyboard focus, before filtering.
 *
 * `[tabindex]` is matched with no value predicate and then filtered by the live `tabIndex`
 * property below, which is what keeps `tabindex="-1"` out of the Tab cycle while leaving it
 * programmatically focusable — the container itself relies on that.
 */
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'area[href]',
  'button',
  'input',
  'select',
  'textarea',
  'summary',
  'audio[controls]',
  'video[controls]',
  'iframe',
  'object',
  'embed',
  '[contenteditable]:not([contenteditable="false"])',
  '[tabindex]',
].join(',');

/**
 * Whether an element is rendered at all.
 *
 * **This is deliberately not a layout test.** The usual idiom —
 * `el.offsetWidth || el.offsetHeight || el.getClientRects().length` — reports *every*
 * element hidden under jsdom, which performs no layout: `offsetWidth` is always 0 and
 * `getClientRects()` is always empty. A trap built on it collects nothing in the test
 * environment, so its whole suite passes vacuously while the real thing may or may not
 * work. Computed style is meaningful in both environments, so that is what is read.
 *
 * The walk stops at the document root, and `hidden` / `inert` are checked alongside
 * `display` and `visibility` because either one takes an element out of the tab order on
 * its own.
 *
 * @param {Element} element
 * @returns {boolean}
 */
function isVisible(element) {
  const view = element.ownerDocument?.defaultView;
  if (!view) return false;

  for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
    if (node.hasAttribute('hidden') || node.hasAttribute('inert')) return false;
    if (node.getAttribute('aria-hidden') === 'true') return false;
    const style = view.getComputedStyle(node);
    if (!style) continue;
    if (style.display === 'none') return false;
    if (style.visibility === 'hidden' || style.visibility === 'collapse') return false;
  }
  return true;
}

/**
 * Whether a candidate belongs in the Tab cycle.
 *
 * @param {Element} element
 * @returns {boolean}
 */
function isTabbable(element) {
  if (element.hasAttribute('disabled') || element.getAttribute('aria-disabled') === 'true') return false;
  // A negative `tabIndex` is focusable by script and not by Tab. That distinction is what
  // lets the container hold focus in the zero-focusable case without joining the cycle, and
  // what keeps a `Drawer`'s scrim clickable without being a tab stop.
  if (typeof element.tabIndex !== 'number' || element.tabIndex < 0) return false;
  // A radio group presents one tab stop, not one per input — but only when one of them is
  // checked. An untouched group is entered at its first member. The group is found by
  // filtering rather than by an attribute selector because a `name` from a caller is not a
  // trusted selector fragment, and `CSS.escape` is not guaranteed in every environment this
  // runs in.
  if (element.type === 'radio' && element.name) {
    const group = [...element.ownerDocument.querySelectorAll('input[type="radio"]')].filter(
      (input) => input.name === element.name,
    );
    const checked = group.find((input) => input.checked);
    if (checked && checked !== element) return false;
  }
  return isVisible(element);
}

/**
 * The container's focusable descendants, in the order Tab visits them.
 *
 * Recomputed by every caller on every keystroke — see "the four awkward cases" in the
 * header. Exported because it is the pure half of this module and can be asserted on
 * directly, without a render.
 *
 * Positive `tabindex` values come first, ascending, then everything at `0` in DOM order,
 * which is the browser's own sequential focus order. Nothing in `ds/` uses a positive
 * `tabindex` (it is an accessibility anti-pattern), but a trap that reordered a caller's
 * DOM would be a trap that behaves differently from the page around it.
 *
 * @param {Element|null|undefined} container
 * @returns {Element[]}
 */
export function collectFocusable(container) {
  if (!container || typeof container.querySelectorAll !== 'function') return [];

  const candidates = [...container.querySelectorAll(FOCUSABLE_SELECTOR)]
    .filter(isTabbable)
    .map((element, index) => ({ element, index }));

  candidates.sort((a, b) => {
    const at = a.element.tabIndex;
    const bt = b.element.tabIndex;
    if (at === bt) return a.index - b.index;
    if (at === 0) return 1;
    if (bt === 0) return -1;
    return at - bt;
  });

  return candidates.map(({ element }) => element);
}

/**
 * Resolve a ref, an element, or a getter to an element.
 *
 * Callers pass refs; tests and the deploy flow may find it easier to pass an element
 * directly. Both are accepted rather than making one of them wrap the other.
 *
 * @param {*} target
 * @returns {Element|null}
 */
function resolveElement(target) {
  if (!target) return null;
  if (typeof target === 'function') return resolveElement(target());
  if (typeof target === 'object' && 'current' in target) return resolveElement(target.current);
  return target.nodeType === 1 ? target : null;
}

/** Focus an element without scrolling the page behind the overlay. */
function focusElement(element) {
  if (!element || typeof element.focus !== 'function') return false;
  element.focus({ preventScroll: true });
  return element.ownerDocument.activeElement === element;
}

/**
 * Trap keyboard focus inside `containerRef` while `active`.
 *
 * @param {Object} options
 * @param {boolean} options.active Whether the overlay is open. Every listener, the
 *   `aria-hidden` marking and the focus restore are keyed off this one flag, so a caller
 *   that renders the overlay conditionally gets the whole behaviour by flipping it.
 * @param {{current: Element|null}} options.containerRef The overlay element. It must carry
 *   `tabIndex={-1}` so it can hold focus when it contains nothing focusable.
 * @param {{current: Element|null}|Element} [options.initialFocusRef] What to focus on open.
 *   Falls back to the first focusable descendant, then to the container. `ConfirmDialog`
 *   points this at cancel; `Drawer` at its close button.
 * @param {Function} [options.onEscape] Called when Escape is pressed. Omit to make Escape
 *   inert — which is what `ConfirmDialog` does while a request is in flight, since a
 *   dialog cannot un-send a POST.
 * @param {{current: Element|null}|Element} [options.returnFocusTo] Where focus goes on
 *   close. Defaults to whatever had focus when the overlay opened.
 * @param {boolean} [options.hideBackground] Whether to `aria-hidden` the rest of the app.
 *   Default `true`.
 * @returns {{focusFirst: Function}} `focusFirst` re-enters the trap at its first focusable
 *   element. The overlay components use it after a step change moves the controls out from
 *   under the user.
 */
export function useFocusTrap({
  active,
  containerRef,
  initialFocusRef,
  onEscape,
  returnFocusTo,
  hideBackground = true,
}) {
  /** What had focus when the trap engaged, so it can be handed back. */
  const openerRef = useRef(null);
  /** Read from the listeners so their identities do not depend on the caller's props. */
  const onEscapeRef = useRef(onEscape);
  onEscapeRef.current = onEscape;
  const initialFocusTargetRef = useRef(initialFocusRef);
  initialFocusTargetRef.current = initialFocusRef;
  const returnFocusRef = useRef(returnFocusTo);
  returnFocusRef.current = returnFocusTo;

  const focusFirst = useCallback(() => {
    const container = containerRef?.current;
    if (!container) return;
    const focusable = collectFocusable(container);
    if (!focusElement(focusable[0])) focusElement(container);
  }, [containerRef]);

  useEffect(() => {
    const container = containerRef?.current;
    if (active !== true || !container) return undefined;

    const doc = container.ownerDocument;

    /*
     * Remember the opener. Guarded two ways, because React 18 StrictMode runs this effect,
     * tears it down and runs it again: by then `activeElement` is whatever the first pass
     * focused, so capturing unconditionally would record the cancel button as the "opener"
     * and restore focus to an element that is about to be unmounted. An `activeElement`
     * inside the container is never the opener, and `<body>` means there was no opener at
     * all — better to leave focus alone on close than to move it to the top of the page.
     */
    const candidate = doc.activeElement;
    if (candidate && candidate !== doc.body && !container.contains(candidate)) {
      openerRef.current = candidate;
    }

    // ── Initial focus ─────────────────────────────────────────────────────
    const nominated = resolveElement(initialFocusTargetRef.current);
    if (!focusElement(nominated)) {
      const focusable = collectFocusable(container);
      // Nothing focusable: the container takes focus itself. It carries `tabIndex={-1}`
      // for exactly this, and Tab below keeps focus here rather than letting it escape.
      if (!focusElement(focusable[0])) focusElement(container);
    }

    // ── Tab / Shift+Tab / Escape ──────────────────────────────────────────
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        const escape = onEscapeRef.current;
        if (typeof escape === 'function') {
          event.preventDefault();
          event.stopPropagation();
          escape(event);
        }
        return;
      }
      if (event.key !== 'Tab') return;

      // Consumed unconditionally. Every branch below decides where focus goes, so the
      // browser's own sequential navigation never runs — this is the line that makes
      // "focus cannot leave" true rather than likely.
      event.preventDefault();

      const focusable = collectFocusable(container);
      if (focusable.length === 0) {
        focusElement(container);
        return;
      }

      // `indexOf` on the *current* list, so an element that has become disabled, or been
      // removed, reads as -1 and the cycle re-enters from the appropriate end.
      const index = focusable.indexOf(doc.activeElement);
      if (index === -1) {
        focusElement(event.shiftKey ? focusable[focusable.length - 1] : focusable[0]);
        return;
      }
      const next = event.shiftKey
        ? (index - 1 + focusable.length) % focusable.length
        : (index + 1) % focusable.length;
      focusElement(focusable[next]);
    };

    /*
     * Focus arriving outside the container by a route the key handler cannot see. Guarded
     * against recursion: the pull-back itself fires `focusin`, and by then the target is
     * inside the container, so the handler returns immediately.
     */
    const onFocusIn = (event) => {
      if (container.contains(event.target)) return;
      const focusable = collectFocusable(container);
      if (!focusElement(focusable[0])) focusElement(container);
    };

    doc.addEventListener('keydown', onKeyDown, true);
    doc.addEventListener('focusin', onFocusIn, true);

    // ── The rest of the app is hidden from assistive technology ───────────
    /** @type {Array<{node: Element, previous: string|null}>} */
    const hidden = [];
    if (hideBackground) {
      for (const node of [...doc.body.children]) {
        // The overlay's own portal root contains the container, so it must stay visible.
        if (node.contains(container)) continue;
        // A node already hidden is left alone, and recorded as such, so restoring cannot
        // reveal something the page had deliberately hidden.
        hidden.push({ node, previous: node.getAttribute('aria-hidden') });
        node.setAttribute('aria-hidden', 'true');
      }
    }

    return () => {
      doc.removeEventListener('keydown', onKeyDown, true);
      doc.removeEventListener('focusin', onFocusIn, true);

      for (const { node, previous } of hidden) {
        if (previous === null) node.removeAttribute('aria-hidden');
        else node.setAttribute('aria-hidden', previous);
      }

      // ── Focus returns to the opener ─────────────────────────────────────
      const explicit = resolveElement(returnFocusRef.current);
      const target = explicit ?? openerRef.current;
      openerRef.current = null;
      // A trigger that has been removed from the document — the row was deleted, the page
      // navigated — cannot be focused. Leaving focus where it is beats focusing `<body>`
      // and beats guessing at a replacement.
      if (target && doc.contains(target)) focusElement(target);
    };
    // `containerRef` is a stable ref object and `hideBackground` never changes for a given
    // overlay. Everything the listeners read that *does* change is read through a ref, so
    // this effect engages once per open rather than re-running mid-overlay and re-stealing
    // focus from whatever the trader had tabbed to.
  }, [active, containerRef, hideBackground]);

  return { focusFirst };
}

export default useFocusTrap;
