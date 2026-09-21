/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/CommandButton — the control a trading action is issued from
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.23. design.md §5.1, §8.3.
 * Requirements 1.2, 15.3, 18.1, 18.4.
 *
 * Wraps `components/ui/Button` and adds the four things a trading action needs and a
 * plain button does not: a state that says a request is in flight, a rule that a
 * disabled control must say why, a rule that a control must have an accessible name,
 * and an optional confirmation step in front of the call.
 *
 * ═══ WHY THIS IS A WRAPPER AND NOT A NEW BUTTON ═══
 *
 * `components/ui/Button` has ~150 call sites including the out-of-scope landing page,
 * and task 6.24 retokens it in place. Restating its variants here would mean two button
 * treatments diverging the moment 6.24 lands. So this component contributes no styling
 * of its own: it maps `intent` onto `ui/Button`'s public `variant`, hands over `icon`,
 * `disabled`, `onClick` and `className`, and adds nothing to the class string.
 *
 * `intent → variant`, and why two intents share one variant:
 *
 *   primary     → primary   secondary → secondary   ghost → ghost
 *   destructive → danger    live      → danger
 *
 * `destructive` is `status.error` and `live` is `env.live`, and in `tokens.css` those
 * are the SAME literal — `--color-env-live` deliberately reuses the loss hue, because
 * live is the state a trader must never mistake and that is the most alarming hue in
 * the palette. So collapsing both onto `danger` loses no colour information. The
 * distinction that does matter is carried where it is actually read: `data-ds-intent`
 * on the rendered control, and the `ConfirmDialog` intent derived from it below.
 *
 * ═══ THE TWO DEV-TIME THROWS ═══
 *
 * **Requirement 15.3 — `disabled` without `disabledReason` throws.** Requirement 15.3
 * says a disabled control SHALL display the reason it is disabled, and a disabled
 * button is the one control a trader cannot interrogate: it does not respond to hover
 * (`ui/Button` sets `pointer-events-none`), it is not focusable, and it looks identical
 * whether the cause is a missing exchange account, an unfinished form or a bug. So the
 * reason is not optional and not a tooltip: it is rendered as visible text beside the
 * control and pointed at by `aria-describedby`. Omitting it fails in development.
 *
 * `loading` does NOT trip that check even though it also renders an inoperable
 * control. The check is on the `disabled` PROP, not on the resolved state, because
 * `loading` already carries its own explanation — `loadingLabel` — and asking a caller
 * for a second one would push them towards `disabledReason="Loading"`, which explains
 * nothing.
 *
 * **Requirement 18.4 — a button with no text needs `aria-label`.** An icon-only button
 * is the common case (a refresh glyph, a close glyph) and it is the case with no
 * accessible name at all: `ui/Button` derives one from `children` only when `children`
 * is a string, so an icon with no children announces as "button". That is the whole of
 * Requirement 18.4's failure mode, so it fails in development too.
 *
 * Both use `ds/devAssert`, so both throw in development — including under the test
 * runner — and log in production, where a missing label is a worse button and not a
 * reason to lose the page it sits on.
 *
 * ═══ THE CONFIRMATION IS OPTIONAL AND ONE-WAY ═══
 *
 * `confirm` is a `ConfirmDialog` spec. Given one, a click opens the dialog and does NOT
 * call `onClick`; `onClick` runs only from the dialog's confirm action. The dialog's
 * `intent` comes from the spec if it names one, otherwise from this button's intent —
 * and only `destructive` and `live` have an intent a dialog can inherit (design.md
 * §5.1). Everything else confirms in `neutral`, because "primary" is not a risk level.
 *
 * The dialog CLOSES on confirm rather than staying open while the request is in flight.
 * That is the simple case, and it is the only case this component owns: §8.3's
 * three-step Configure → Review → AckLive flow drives `ConfirmDialog` directly (task
 * 10.5), where the flow — not the button — holds the step and the busy state.
 *
 * `ConfirmDialog` deliberately does not import this component (see its docblock), so
 * this import closes no cycle.
 *
 * ═══ NO WRAPPER ELEMENT ═══
 *
 * The root is the button itself. The disabled reason and the dialog are siblings under
 * a fragment rather than children of a wrapper `<span>`, so `className` and every
 * forwarded prop land on the control a caller expects them to land on, and a page's
 * flex or grid row sees a button where it put one. The dialog is portalled, so its
 * position in the tree costs no layout.
 */

import { useCallback, useId, useState } from 'react';

import { Button } from '../ui/Button';

import { ConfirmDialog } from './ConfirmDialog';
import { assertContract, hasText } from './devAssert';
import { Spinner } from './LoadingState';

/** design.md §5.1's five intents. */
export const COMMAND_INTENTS = Object.freeze([
  'primary',
  'secondary',
  'ghost',
  'destructive',
  'live',
]);

/**
 * intent → `ui/Button`'s public `variant`. The only styling decision in this file, and
 * it is a lookup rather than a class string. See the docblock for why `destructive` and
 * `live` share `danger`.
 */
const INTENT_VARIANT = Object.freeze({
  primary: 'primary',
  secondary: 'secondary',
  ghost: 'ghost',
  destructive: 'danger',
  live: 'danger',
});

/**
 * The intents a `ConfirmDialog` can inherit (design.md §5.1). Anything else confirms
 * `neutral`: a calm confirmation's confirm button is the page's primary action, and
 * "primary" is not a risk level.
 */
const INHERITABLE_CONFIRM_INTENT = Object.freeze({
  destructive: 'destructive',
  live: 'live',
});

/**
 * Whether `children` will render something a screen reader can read as this control's
 * name.
 *
 * Strings and numbers are inspected; an element is taken at its word, because this
 * cannot see inside one. That asymmetry is deliberate and it is the conservative
 * direction for a dev-time throw: `<CommandButton icon={X}><span>Close</span></...>`
 * has a name and must not be failed for it, whereas the case Requirement 18.4 exists
 * for — an icon and no children at all — is caught exactly.
 *
 * @param {unknown} node
 * @returns {boolean}
 */
export function hasAccessibleText(node) {
  if (node === null || node === undefined || typeof node === 'boolean') return false;
  if (typeof node === 'string') return node.trim() !== '';
  if (typeof node === 'number') return Number.isFinite(node);
  if (Array.isArray(node)) return node.some(hasAccessibleText);
  return true;
}

/**
 * The command surface.
 *
 * @param {Object} props
 * @param {'primary'|'secondary'|'ghost'|'destructive'|'live'} [props.intent] Default
 *   `primary`. An unrecognised value throws in development and renders `primary`.
 * @param {boolean} [props.loading] A request issued from this control is in flight. The
 *   control is inoperable, reads `loadingLabel`, spins, and is marked `aria-busy`.
 * @param {string} [props.loadingLabel] What the control reads while `loading`. Falls
 *   back to `children`, so a caller who omits it gets a spinner rather than a blank.
 * @param {boolean} [props.disabled] The action is unavailable. REQUIRES
 *   `disabledReason` (Requirement 15.3).
 * @param {string} [props.disabledReason] Why the action is unavailable, in a trader's
 *   terms. Rendered as visible text and referenced by `aria-describedby`.
 * @param {Object} [props.confirm] A `ConfirmDialog` spec. Its presence puts the dialog
 *   in front of `onClick`; see the docblock.
 * @param {React.ComponentType} [props.icon] A lucide icon. Icon-only REQUIRES
 *   `aria-label` (Requirement 18.4).
 * @param {Function} [props.onClick] The action. Called on activation, or from the
 *   confirmation's confirm action when `confirm` is given.
 * @param {React.ReactNode} [props.children] The label.
 * @param {string} [props.className] Appended to the control's own classes.
 */
export function CommandButton({
  intent = 'primary',
  loading = false,
  loadingLabel,
  disabled = false,
  disabledReason,
  confirm,
  icon,
  onClick,
  children,
  className = '',
  'aria-label': ariaLabel,
  ...rest
}) {
  const reasonId = useId();
  const [confirming, setConfirming] = useState(false);

  const known = COMMAND_INTENTS.includes(intent);
  assertContract(
    known,
    `CommandButton: \`intent\` must be one of ${COMMAND_INTENTS.join(' | ')}, received `
      + `${JSON.stringify(intent)}. Colour is derived from the intent — this component takes no `
      + 'colour prop.',
  );
  const resolvedIntent = known ? intent : 'primary';

  const isLoading = loading === true;
  const isDisabled = disabled === true;

  // ── Requirement 15.3 ────────────────────────────────────────────────────
  // On the `disabled` prop, not on the resolved state: `loading` explains itself
  // through `loadingLabel`. See the docblock.
  assertContract(
    !isDisabled || hasText(disabledReason),
    'CommandButton is `disabled` but carries no `disabledReason`. Requirement 15.3: a '
      + 'disabled control must display why. A disabled button cannot be hovered, focused or '
      + 'interrogated, so without the reason a trader cannot tell a missing exchange account '
      + 'from a bug. Pass `disabledReason="Connect an exchange account first"` — or leave the '
      + 'control enabled and fail the action with a reason instead.',
  );

  const labelledByText = hasAccessibleText(isLoading ? (loadingLabel ?? children) : children);

  // ── Requirement 18.4 ────────────────────────────────────────────────────
  assertContract(
    labelledByText || hasText(ariaLabel),
    icon
      ? 'CommandButton renders an icon with no text and no `aria-label`. Requirement 18.4: '
        + 'every interactive control needs an accessible name, and an icon-only button has '
        + 'none — it announces as "button". Pass `aria-label="Refresh positions"`.'
      : 'CommandButton has neither children nor an `aria-label`, so it has no accessible '
        + 'name at all (Requirement 18.4). Give it a label, or an `aria-label` if the label '
        + 'is carried by something this component cannot see.',
  );

  const showsReason = isDisabled && hasText(disabledReason);
  const hasConfirmSpec = Boolean(confirm) && typeof confirm === 'object';
  const inoperable = isDisabled || isLoading;

  const handleClick = useCallback(
    (event) => {
      /*
       * `disabled` on the element is a rendering; this is the rule. A programmatic
       * `.click()` or a stray key handler must not be able to place an order from a
       * control the trader can see is unavailable. `ConfirmDialog`'s confirm handler
       * makes the same argument for the same reason.
       */
      if (inoperable) return;
      if (hasConfirmSpec) {
        setConfirming(true);
        return;
      }
      if (typeof onClick === 'function') onClick(event);
    },
    [inoperable, hasConfirmSpec, onClick],
  );

  const handleCancel = useCallback(() => {
    setConfirming(false);
    if (typeof confirm?.onCancel === 'function') confirm.onCancel();
  }, [confirm]);

  const handleConfirm = useCallback(() => {
    setConfirming(false);
    if (typeof onClick === 'function') onClick();
  }, [onClick]);

  return (
    <>
      <Button
        variant={INTENT_VARIANT[resolvedIntent]}
        disabled={inoperable}
        onClick={handleClick}
        /*
         * `ui/Button` spins an icon whose `displayName` is `Loader2`. At the pinned
         * lucide version that icon is named `LoaderCircle`, so that check never fires and
         * a lucide spinner handed over here would sit still. `ui/Button` belongs to task
         * 6.24, so rather than edit it or restate its animation class, the icon passed is
         * `LoadingState`'s spinner — the one rotating affordance in the design system,
         * whose keyframes live in `styles/ds.css` and whose colour is `currentColor`.
         */
        icon={isLoading ? Spinner : icon}
        aria-label={ariaLabel}
        aria-busy={isLoading ? true : undefined}
        aria-describedby={showsReason ? reasonId : undefined}
        data-ds="command-button"
        data-ds-intent={resolvedIntent}
        className={className}
        {...rest}
      >
        {isLoading ? (loadingLabel ?? children) : children}
      </Button>

      {/* Requirement 15.3. Visible text rather than a `title`: a disabled button has
          `pointer-events-none`, so a native tooltip on it can never be shown, and it is
          not focusable, so nothing else can reveal it either. */}
      {showsReason ? (
        <span
          id={reasonId}
          data-ds="command-button-reason"
          className="text-micro text-content-secondary"
        >
          {disabledReason}
        </span>
      ) : null}

      {hasConfirmSpec && confirming ? (
        <ConfirmDialog
          {...confirm}
          open
          intent={
            confirm.intent
            ?? INHERITABLE_CONFIRM_INTENT[resolvedIntent]
            ?? 'neutral'
          }
          busy={isLoading}
          onCancel={handleCancel}
          onConfirm={handleConfirm}
        />
      ) : null}
    </>
  );
}

export default CommandButton;
