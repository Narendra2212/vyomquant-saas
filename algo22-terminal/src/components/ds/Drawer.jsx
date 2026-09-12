/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Drawer — the sliding overlay panel (design §5.2, §6.4, §11.6)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.20. Requirements 17.3, 17.4, 18.1, 18.2, 18.3, 18.4.
 * Properties P34, P35.
 *
 * WHAT IT SHARES WITH `ConfirmDialog`, AND WHY THAT MATTERS
 * --------------------------------------------------------
 * The focus trap (`hooks/useFocusTrap.js`), Escape handling and the overlay registry
 * (`./overlayRegistry.js`) are the same three modules, not reimplementations. §11.6 puts the
 * one-overlay rule on both components together, and §11.7 puts the focus trap on both
 * together — two copies of either would let the pair disagree, and the pair disagreeing is
 * exactly the failure Requirement 17.3 describes: an overlay a keyboard user can see and
 * cannot reach.
 *
 * WHO USES IT, AND WHY IT STAYS GENERAL
 * -------------------------------------
 * Two consumers are already known and they want different placements, so the placement is a
 * prop rather than a fork:
 *
 *   * **The account menu (task 8.8).** §6.4 describes it as "a `ds/Drawer`-family component
 *     with the same focus trap and `Escape` handling as `ConfirmDialog`". A side panel.
 *   * **The Strategy Builder's tablet inspector (task 24.7).** §11.6's tablet review mode:
 *     "The inspector opens as a bottom `Drawer` instead of a side track, so a tapped node's
 *     parameters are readable". A bottom panel.
 *
 * So `placement` is `'right' | 'left' | 'bottom'`. Nothing else is added speculatively.
 *
 * THE SCRIM IS A REAL BUTTON
 * --------------------------
 * Not a `div` with `onClick`. §11.7 and task 6.27 put `eslint-plugin-jsx-a11y-x` at *error*
 * for `src/components/ds/**`, and "no `div onClick` on any interactive element" is the rule
 * it enforces. A click-to-dismiss scrim *is* an interactive element, so it is a `<button>`
 * with an accessible name. It carries `tabIndex={-1}` so it is clickable without becoming
 * the drawer's first tab stop — `useFocusTrap`'s tabbability filter excludes negative
 * `tabIndex`, which is what makes that work — and the header's close button is the real,
 * reachable dismissal for a keyboard user.
 *
 * INITIAL FOCUS IS ON CLOSE
 * -------------------------
 * The same reasoning as `ConfirmDialog`'s cancel: focus lands on the least consequential
 * control. A drawer's content may hold anything, including the Builder inspector's fields;
 * dropping focus into the first of those would move a trader's caret somewhere they did not
 * ask for.
 *
 * VIEWPORT CLAMP (Requirement 17.3, P34)
 * --------------------------------------
 * Side drawers: `max-width: min(420px, calc(100vw - 2 * var(--spacing-4)))`, full height.
 * Bottom drawer: `max-height: calc(100dvh - 2 * var(--spacing-8))`, full width. Both have a
 * single internal scroll region, so content length cannot push the panel past the clamp.
 * Rendered through a portal at `var(--z-drawer)` — below `--z-modal`, which is the ordering
 * `tokens.css` declares and the reason a drawer can never paint over a confirmation even if
 * the registry were bypassed.
 */

import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

import { cssVar, token } from '../../design/tokens';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { OVERLAY_KIND, releaseOverlay, requestOverlay } from './overlayRegistry';

/** Read at call time — see the note in `overlayRegistry.js`. */
function isDevelopment() {
  return import.meta.env.DEV === true;
}

/** Throws in development, logs in production. The `ds/` contract-violation idiom. */
function contractError(message) {
  if (isDevelopment()) throw new Error(`Drawer: ${message}`);
  console.error(`[ds/Drawer] ${message}`);
}

/** The placements this component supports. Two sides and one bottom. */
export const DRAWER_PLACEMENTS = Object.freeze(['right', 'left', 'bottom']);

/**
 * Geometry per placement, stated in viewport units so the clamp holds at every width without
 * naming a breakpoint.
 */
const PLACEMENT_GEOMETRY = Object.freeze({
  right: Object.freeze({
    inset: Object.freeze({ top: 0, right: 0, bottom: 0 }),
    height: '100dvh',
    maxWidth: `min(420px, calc(100vw - 2 * ${cssVar('spacing.4')}))`,
    width: '100%',
    borderSide: 'borderLeft',
    radius: `${cssVar('radius.lg')} 0 0 ${cssVar('radius.lg')}`,
  }),
  left: Object.freeze({
    inset: Object.freeze({ top: 0, left: 0, bottom: 0 }),
    height: '100dvh',
    maxWidth: `min(420px, calc(100vw - 2 * ${cssVar('spacing.4')}))`,
    width: '100%',
    borderSide: 'borderRight',
    radius: `0 ${cssVar('radius.lg')} ${cssVar('radius.lg')} 0`,
  }),
  bottom: Object.freeze({
    inset: Object.freeze({ left: 0, right: 0, bottom: 0 }),
    maxHeight: `calc(100dvh - 2 * ${cssVar('spacing.8')})`,
    width: '100%',
    borderSide: 'borderTop',
    radius: `${cssVar('radius.lg')} ${cssVar('radius.lg')} 0 0`,
  }),
});

/**
 * A focus-trapped, single-instance overlay panel.
 *
 * @param {Object} props
 * @param {boolean} props.open Requested, not guaranteed — the overlay registry decides
 *   (Requirement 17.3).
 * @param {Function} props.onClose Called by the close button, by Escape, and by the scrim
 *   when `dismissOnScrim` is true. Required.
 * @param {string} [props.title] Rendered as the panel heading and used as its accessible
 *   name via `aria-labelledby`.
 * @param {string} [props.ariaLabel] The accessible name when there is no visible `title`.
 *   One of `title` / `ariaLabel` is required (Requirement 18.4).
 * @param {'right'|'left'|'bottom'} [props.placement] Default `'right'`.
 * @param {string} [props.closeLabel] Accessible name for the close button and the scrim.
 *   Default `'Close'`.
 * @param {boolean} [props.dismissOnScrim] Whether clicking outside closes the drawer.
 *   Default `true`. A drawer is a navigational surface, not a decision point — unlike
 *   `ConfirmDialog`, which deliberately has no outside-click dismissal.
 * @param {React.ReactNode} [props.footer] Pinned below the scroll region.
 * @param {React.ReactNode} [props.children] The panel body. The only scroll region.
 */
export function Drawer({
  open,
  onClose,
  title,
  ariaLabel,
  placement = 'right',
  closeLabel = 'Close',
  dismissOnScrim = true,
  footer,
  children,
}) {
  const instanceId = useId();
  const titleId = `${instanceId}-title`;

  const panelRef = useRef(null);
  const closeRef = useRef(null);

  const geometry = PLACEMENT_GEOMETRY[placement] ?? PLACEMENT_GEOMETRY.right;
  const hasTitle = typeof title === 'string' && title.trim() !== '';
  const hasAriaLabel = typeof ariaLabel === 'string' && ariaLabel.trim() !== '';

  /* The claim. Same shape and same reasoning as `ConfirmDialog`'s — see that file's header. */
  const labelRef = useRef(title ?? ariaLabel);
  labelRef.current = hasTitle ? title : ariaLabel;
  const [granted, setGranted] = useState(false);
  useEffect(() => {
    if (open !== true) {
      setGranted(false);
      return undefined;
    }
    const allowed = requestOverlay({
      id: instanceId,
      kind: OVERLAY_KIND.DRAWER,
      label: labelRef.current,
    });
    setGranted(allowed);
    return () => {
      releaseOverlay(instanceId);
      setGranted(false);
    };
  }, [open, instanceId]);

  const active = open === true && granted === true;

  // ── The contract checks, collected purely and reported from an effect ────
  const violations = [];
  if (typeof onClose !== 'function') {
    violations.push('`onClose` is required — a drawer with no way out traps focus permanently.');
  }
  if (!hasTitle && !hasAriaLabel) {
    violations.push(
      'one of `title` / `ariaLabel` is required. A dialog with no accessible name is '
        + 'announced as "dialog" and nothing else (Requirement 18.4).',
    );
  }
  if (!DRAWER_PLACEMENTS.includes(placement)) {
    violations.push(
      `\`placement\` must be one of ${DRAWER_PLACEMENTS.join(', ')}; received "${placement}". `
        + 'Rendering on the right.',
    );
  }
  const violationsRef = useRef(violations);
  violationsRef.current = violations;
  const violationKey = violations.join('\n');
  useEffect(() => {
    if (!active || violationsRef.current.length === 0) return;
    contractError(violationsRef.current.join(' '));
  }, [active, violationKey]);

  const handleClose = useCallback(() => {
    if (typeof onClose === 'function') onClose();
  }, [onClose]);

  useFocusTrap({
    active,
    containerRef: panelRef,
    initialFocusRef: closeRef,
    onEscape: handleClose,
  });

  if (!active) return null;

  return createPortal(
    <div
      data-ds="drawer-layer"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: cssVar('z.drawer'),
      }}
    >
      {/*
        The scrim. A real `<button>` rather than a `div onClick` (see the header). It is not a
        tab stop — the header's close button is the keyboard route out — but it is a genuine
        control for a pointer, and it carries a name so it is not an unlabelled button in the
        accessibility tree.
      */}
      <button
        type="button"
        aria-label={closeLabel}
        tabIndex={-1}
        onClick={dismissOnScrim ? handleClose : undefined}
        disabled={!dismissOnScrim}
        data-ds="drawer-scrim"
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          padding: 0,
          border: 'none',
          background: cssVar('color.surface.overlay'),
          cursor: dismissOnScrim ? 'pointer' : 'default',
        }}
      />

      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={hasTitle ? titleId : undefined}
        aria-label={hasTitle ? undefined : ariaLabel}
        data-ds="drawer"
        data-ds-overlay={OVERLAY_KIND.DRAWER}
        data-ds-placement={DRAWER_PLACEMENTS.includes(placement) ? placement : 'right'}
        // Holds focus when the drawer contains nothing focusable. Negative, so not a tab stop.
        tabIndex={-1}
        style={{
          position: 'absolute',
          ...geometry.inset,
          // ── Requirement 17.3 / P34 ──
          ...(geometry.maxWidth ? { maxWidth: geometry.maxWidth } : {}),
          ...(geometry.maxHeight ? { maxHeight: geometry.maxHeight } : {}),
          ...(geometry.height ? { height: geometry.height } : {}),
          width: geometry.width,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          background: cssVar('color.surface.panel'),
          [geometry.borderSide]: `1px solid ${cssVar('color.line.default')}`,
          borderRadius: geometry.radius,
          boxShadow: cssVar('shadow.overlay'),
          color: cssVar('color.content.primary'),
          fontFamily: cssVar('font.sans'),
          fontSize: cssVar('text.body'),
        }}
      >
        <div
          style={{
            flex: '0 0 auto',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: cssVar('spacing.3'),
            padding: `${cssVar('spacing.3')} ${cssVar('spacing.4')}`,
            borderBottom: `1px solid ${cssVar('color.line.subtle')}`,
          }}
        >
          {hasTitle ? (
            <h2
              id={titleId}
              style={{
                margin: 0,
                fontSize: cssVar('text.title'),
                // `--text-title--line-height` carries a double dash, which `cssVar` cannot
                // express; read the generated mirror instead.
                lineHeight: token.text.lineHeight.title,
                fontWeight: 600,
              }}
            >
              {title}
            </h2>
          ) : (
            <span />
          )}
          <button
            ref={closeRef}
            type="button"
            onClick={handleClose}
            aria-label={closeLabel}
            data-ds="drawer-close"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: cssVar('spacing.1'),
              background: 'transparent',
              border: `1px solid ${cssVar('color.line.default')}`,
              borderRadius: cssVar('radius.sm'),
              color: cssVar('color.content.secondary'),
              cursor: 'pointer',
              flex: '0 0 auto',
            }}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <div
          data-ds="drawer-body"
          style={{
            // See `ConfirmDialog` on why `min-height: 0` is what makes this scroll rather
            // than pushing the panel past its clamp.
            minHeight: 0,
            overflowY: 'auto',
            padding: cssVar('spacing.4'),
          }}
        >
          {children}
        </div>

        {footer === null || footer === undefined ? null : (
          <div
            data-ds="drawer-footer"
            style={{
              flex: '0 0 auto',
              padding: cssVar('spacing.4'),
              borderTop: `1px solid ${cssVar('color.line.subtle')}`,
            }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

export default Drawer;
