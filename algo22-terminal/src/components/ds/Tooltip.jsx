/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/Tooltip — a hint a keyboard user can actually reach
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.23. design.md §5.2, §11.4.
 * Requirements 1.2, 18.1, 18.2, 18.4.
 *
 * ═══ HOVER-ONLY IS THE DEFECT, NOT THE FEATURE ═══
 *
 * A tooltip that appears on `mouseenter` and nowhere else is invisible to a trader who
 * does not use a pointer, and every hint this app wants to put in one is load-bearing:
 * the derivation behind "Invested (capital in use)" (§7.6 — the field is
 * `used_balance`, and labelling it "invested capital" without saying so misrepresents
 * it), the scope of "Realised P&L (today)", the reason a figure is not available. Those
 * are not decoration; they are the difference between reading a number correctly and
 * misreading it.
 *
 * So the trigger opens on **`focus` as well as `mouseenter`**, and closes on `blur`,
 * `mouseleave` and `Escape`. §11.4 makes the same demand of `ds/Chart`'s tooltip for the
 * same reason, and Requirement 18.1's "operable via keyboard" is not satisfiable by a
 * surface that only a mouse can summon.
 *
 * Hover and focus are tracked as two INDEPENDENT reasons to be open, not one boolean.
 * Collapsing them means moving the mouse away closes a tooltip that keyboard focus is
 * still holding open, which is the bug every hand-rolled tooltip has.
 *
 * `Escape` is a document-level listener while open, not a key handler on the trigger.
 * That is deliberate on two counts: it dismisses a tooltip that a *pointer* opened
 * (where focus may be somewhere else entirely), and it keeps the wrapper free of
 * `onKeyDown`, so the wrapper is not a static element carrying keyboard interactions —
 * the shape task 6.27's `jsx-a11y-x`-at-error rules exist to refuse.
 *
 * ═══ `aria-describedby`, NEVER `aria-label` ═══
 *
 * The hint is attached to the trigger with `aria-describedby`. Putting it in
 * `aria-label` would REPLACE the trigger's accessible name with the explanation, so
 * "Total P&L" with a hint of "Realised + unrealised since account open" would be
 * announced as the explanation and the trader would never hear what the figure is.
 * Requirement 18.4 asks for an accessible name on every interactive control; a
 * description is an addition to that name, not a substitute for it.
 *
 * A caller's own `aria-describedby` survives — the ids are concatenated, because
 * `ds/Field` already describes its controls with a hint and an error id and a tooltip
 * must not evict either.
 *
 * ═══ THE BUBBLE IS ALWAYS IN THE DOM ═══
 *
 * `aria-describedby` pointing at an element that only exists while the tooltip is open
 * is a reference that resolves to nothing for as long as it matters. So there is ONE
 * bubble element, always rendered, carrying the id: `sr-only` while closed, and the
 * positioned visible bubble while open. A screen-reader user gets the description the
 * moment the trigger takes focus, whether or not the visual bubble is showing; a sighted
 * keyboard user sees it. Neither depends on the other working.
 *
 * That is also `ds/Metric`'s existing arrangement, which is what "stay compatible" means
 * here. `Metric`'s `hint` renders as `title` + `aria-describedby` pointing at a
 * permanent `sr-only` span, and a later change to
 *
 *     <Tooltip content={hint}><span …>{label}</span></Tooltip>
 *
 * keeps that contract exactly — same attribute, same description text, same DOM
 * position — and adds the visible-on-focus bubble that `title` cannot provide. Nothing
 * in `Metric` changes shape; this task does not make that edit.
 *
 * ═══ WHY THE TRIGGER MAY GAIN A TAB STOP ═══
 *
 * Most tooltip triggers in this app are not buttons. `Metric`'s is a `<span>` label;
 * §7.6's derivation note hangs off a table header. A `<span>` cannot receive focus, so
 * `aria-describedby` on it is never announced and the whole point is lost.
 *
 * So a trigger that is not natively focusable is given `tabIndex={0}`. That is the
 * documented WAI-ARIA arrangement for a tooltip on non-interactive content, and the
 * trade is explicit: one extra tab stop per hint, in exchange for the hint existing at
 * all for a keyboard user. A trigger that is already focusable — a button, a link, a
 * form control, or anything the caller has given its own `tabIndex` — is left alone, so
 * no control gains a duplicate stop.
 *
 * An absent or empty `content` renders the children untouched: no bubble, no
 * `aria-describedby`, and no tab stop. A tooltip with nothing to say must not cost a
 * keystroke, which is what keeps `<Tooltip content={maybeHint}>` safe to write when the
 * hint is optional.
 *
 * @module components/ds/Tooltip
 */

import { cloneElement, isValidElement, useCallback, useEffect, useId, useState } from 'react';

import { cssVar } from '../../design/tokens';

import { assertContract, hasText } from './devAssert';

/** Where the bubble sits relative to its trigger. */
export const TOOLTIP_PLACEMENTS = Object.freeze(['top', 'bottom']);

/**
 * Element types that can already receive focus, so must not be given a `tabIndex`.
 *
 * `a` is deliberately in the list even though a bare `<a>` with no `href` is not
 * focusable: an anchor without a destination is a defect of its own, and quietly making
 * it a tab stop would hide that rather than fix it.
 */
const NATIVELY_FOCUSABLE = Object.freeze(['a', 'button', 'input', 'select', 'textarea', 'summary']);

/**
 * Whether this trigger needs `tabIndex={0}` to be reachable.
 *
 * A custom component (`type` is a function or a `forwardRef` object rather than a tag
 * name) is left alone: it may render a button, and forcing a `tabIndex` through it would
 * either be ignored or produce a second tab stop on something already focusable.
 */
function needsTabStop(element) {
  const props = element.props || {};
  if (props.tabIndex !== undefined) return false;
  if (props.disabled === true) return false;
  if (typeof element.type !== 'string') return false;
  return !NATIVELY_FOCUSABLE.includes(element.type);
}

/** Geometry per placement, in token spacing. Colour and radius come from tokens too. */
const PLACEMENT_STYLE = Object.freeze({
  top: Object.freeze({ bottom: `calc(100% + ${cssVar('spacing.1')})` }),
  bottom: Object.freeze({ top: `calc(100% + ${cssVar('spacing.1')})` }),
});

/**
 * A hint attached to a trigger, reachable by pointer and by keyboard.
 *
 * @param {Object} props
 * @param {React.ReactNode} props.content The hint. A string, or a node for hints that
 *   need structure. Empty or absent renders `children` untouched.
 * @param {React.ReactElement} props.children REQUIRED — exactly one element, the
 *   trigger. It is cloned to receive `aria-describedby`, and `tabIndex={0}` when it is
 *   not already focusable.
 * @param {'top'|'bottom'} [props.placement] Default `'top'`.
 * @param {string} [props.className] Appended to the wrapper, not to the bubble.
 */
export function Tooltip({ content, children, placement = 'top', className = '', ...rest }) {
  const generatedId = useId();
  const tipId = `${generatedId}tooltip`;

  // Two independent reasons to be open, plus one to be shut. Collapsing hover and focus
  // into a single boolean is what makes a tooltip close under the pointer while keyboard
  // focus is still on the trigger. See the module docblock.
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  const speaks = content !== null && content !== undefined && content !== false && content !== '';
  const open = speaks && (hovered || focused) && !dismissed;

  const show = useCallback(() => setDismissed(false), []);

  // Escape, at the document. Attached only while open, so this component adds no
  // listener at rest — a page with forty tooltips would otherwise carry forty.
  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === 'Escape') setDismissed(true);
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open]);

  assertContract(
    isValidElement(children),
    'Tooltip: `children` must be exactly one element — it is the trigger, and it is what '
      + 'receives `aria-describedby` and, when it is not already focusable, `tabIndex={0}`. '
      + 'A bare string cannot take focus, so a tooltip on one is unreachable by keyboard '
      + '(Requirement 18.1). Wrap the text: `<Tooltip content={hint}><span>{label}</span></Tooltip>`.',
  );

  // Nothing to say: the children pass through with no wrapper, no description and no tab
  // stop. `<Tooltip content={maybeHint}>` is safe to write for an optional hint.
  if (!speaks || !isValidElement(children)) return children ?? null;

  const existingDescription = children.props['aria-describedby'];
  const trigger = cloneElement(children, {
    // Concatenated, not replaced: `ds/Field` already describes its controls with a hint
    // and an error id, and a tooltip must not evict either.
    'aria-describedby': hasText(existingDescription)
      ? `${existingDescription.trim()} ${tipId}`
      : tipId,
    ...(needsTabStop(children) ? { tabIndex: 0 } : {}),
  });

  const resolvedPlacement = TOOLTIP_PLACEMENTS.includes(placement) ? placement : 'top';

  return (
    <span
      data-ds="tooltip-trigger"
      data-tooltip-open={open ? 'true' : 'false'}
      className={`relative inline-flex ${className}`.trim()}
      {...rest}
      /* Spread ABOVE the handlers, not below: these four are the whole mechanism, and a
         caller passing an `onMouseEnter` of their own must not be able to replace the one
         that opens the tooltip. */
      // `onFocus` / `onBlur` on the wrapper rather than on the trigger itself: React maps
      // them to `focusin` / `focusout`, which bubble, so they fire for the cloned child
      // whatever it turns out to be — including a component that renders its own button.
      onFocus={() => {
        show();
        setFocused(true);
      }}
      onBlur={() => setFocused(false)}
      onMouseEnter={() => {
        show();
        setHovered(true);
      }}
      onMouseLeave={() => setHovered(false)}
    >
      {trigger}

      {/* ONE bubble, always in the document, so `aria-describedby` always resolves.
          `sr-only` while closed: available to a screen reader the moment the trigger
          takes focus, and invisible until hover or focus asks for it. Never
          `aria-hidden` — that would make the description unreadable, which is the whole
          purpose of the element. */}
      <span
        id={tipId}
        role="tooltip"
        data-ds="tooltip"
        data-tooltip-open={open ? 'true' : 'false'}
        className={open ? '' : 'sr-only'}
        style={
          open
            ? {
              ...PLACEMENT_STYLE[resolvedPlacement],
              // Positioned inline rather than through `absolute left-1/2 w-max z-dropdown`:
              // three of those four utilities have no other call site in `src/`, and a
              // class that exists only here is a class the `dead-tailwind` guard has to
              // reason about for no benefit. The `z-index` is still the token.
              position: 'absolute',
              left: '50%',
              width: 'max-content',
              // Centred on the trigger. `translateX` rather than a flex parent because
              // the wrapper is `inline-flex` around the trigger itself and must not be
              // widened by the bubble.
              transform: 'translateX(-50%)',
              maxWidth: '18rem',
              // The bubble is a passive surface: it must not swallow the `mouseleave`
              // that closes it, and it holds nothing clickable.
              pointerEvents: 'none',
              zIndex: cssVar('z.dropdown'),
              padding: `${cssVar('spacing.1')} ${cssVar('spacing.2')}`,
              borderRadius: cssVar('radius.sm'),
              border: `1px solid ${cssVar('color.line.strong')}`,
              background: cssVar('color.surface.raised'),
              color: cssVar('color.content.primary'),
              boxShadow: cssVar('shadow.raised'),
              fontFamily: cssVar('font.sans'),
              fontSize: cssVar('text.small'),
              // Wraps rather than truncating: a hint that explains a derivation is a
              // sentence, and half a sentence is worse than none.
              whiteSpace: 'normal',
            }
            : undefined
        }
      >
        {content}
      </span>
    </span>
  );
}

export default Tooltip;
