/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ConfirmDialog — the ONLY confirmation surface (design §5.1, §8.3, §8.4)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.20.
 * Requirements 7.6, 8.1, 8.2, 8.3, 8.5, 17.3, 18.1, 18.2, 18.3, 18.4.
 * Properties P34, P35.
 *
 * WHAT THIS REPLACES
 * ------------------
 * Six native browser dialogs (design §1.8): five `window.confirm` and one `window.prompt`,
 * sitting on deploy, delete, restore-version and deploy-version. Nothing like this component
 * exists in the tree today, and the native dialogs cannot be made to do the job:
 *
 *   * `window.confirm` **cannot be focus-trapped** by us, cannot be styled, and cannot carry
 *     the eight-field review Requirement 8.1 demands. It is one string and two buttons.
 *   * `window.prompt` **cannot carry a label** or inline validation, so Requirements 15.1
 *     and 15.2 are unreachable through it.
 *   * Neither can show an environment badge, so Requirement 8.5 — a live confirmation must
 *     be visually distinguishable from a paper one — is unreachable through them too.
 *
 * This component is the last thing between a trader and an accidental live order, so the
 * decisions below are conservative on purpose and each one says why.
 *
 * THE FOUR SAFETY DECISIONS
 * -------------------------
 *   1. **Initial focus is on CANCEL.** Not confirm. A trader who opens the dialog and hits
 *      Enter or Space out of habit lands on "no". Requirement 8.3 asks for "an explicit
 *      trader action"; a default that submits is the opposite of explicit.
 *   2. **The acknowledgement gates confirm in two independent places** — the button's
 *      `disabled` attribute *and* a guard inside the confirm handler. A `disabled` button is
 *      a rendering; the guard is the rule. Requirement 8.3 says the request is not submitted
 *      before the explicit action, and one `.click()` from a stray effect or a test helper
 *      must not be able to make that false.
 *   3. **A missing review field renders the not-available marker.** It is never omitted and
 *      never defaulted. §8.3: "A field the configuration does not carry renders the
 *      not-available marker; the flow is not blocked by it, but it is never silently
 *      defaulted." A blank where "estimated exposure" should be is information; a fabricated
 *      zero is a lie, and an omitted row is a lie by silence.
 *   4. **A malformed acknowledgement fails CLOSED.** If a caller asks for an acknowledgement
 *      and gets its shape wrong, the gate stays on: development throws, and production keeps
 *      the checkbox with fallback copy that claims nothing. The worst production outcome is
 *      then a confirmation the trader cannot complete — which, for a live order, is the safe
 *      direction. Quietly dropping a requested gate is not.
 *
 * WHAT THIS COMPONENT DOES **NOT** DO
 * -----------------------------------
 * It holds no flow state. §8.3's three-step Configure → Review → AckLive machine is task
 * 10.5's, and it drives this component from the outside: `title`, `review`, `acknowledgement`
 * and `children` all change per step, and `AckLive` is expressed by passing an
 * `acknowledgement` at all. That keeps the "no code path submits before the acknowledgement"
 * argument checkable in one place — here, in {@link ConfirmDialog}'s confirm handler — rather
 * than spread across a state machine.
 *
 * It also issues no request, chooses no endpoint and knows nothing about deployment gating
 * (Requirement 19.1). `onConfirm` is the caller's.
 *
 * TWO DELIBERATE NON-DEPENDENCIES
 * -------------------------------
 *   * **`CommandButton` is not used here**, even though it is this system's button. §5.1
 *     gives `CommandButton` an optional `confirm` prop that opens a `ConfirmDialog`, so
 *     importing it would close a module cycle. The two buttons below are plain `<button>`
 *     elements taking their colour from `design/semantic.js`, which is what `CommandButton`
 *     will do too.
 *   * **`ds/ErrorState` (task 6.1) is still being built concurrently.** Rather than block,
 *     its treatment is rendered inline from the module that owns the content —
 *     `design/errorCopy.js`'s `translateError` — so no copy is invented here, only laid
 *     out. That fallback is marked `SWAP WHEN …` below. The environment badge no longer
 *     needs one: task 6.7 has landed and `ds/TradingEnvironmentBadge` is imported.
 *
 * VIEWPORT CLAMP (Requirement 17.3, P34)
 * --------------------------------------
 * `max-height: calc(100dvh - 2 * var(--spacing-8))` with the body as the only scroll region,
 * and `max-width: min(560px, calc(100vw - 2 * var(--spacing-4)))`. Both are stated in the
 * viewport's own units, so there is no width at which the dialog can exceed the viewport —
 * the clamp does not depend on a breakpoint being listed anywhere. Rendered through a portal
 * at `var(--z-modal)` so no ancestor's `overflow` can clip it, and registered with
 * `overlayRegistry` so it cannot overlap another overlay.
 */

import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertOctagon } from 'lucide-react';

import { token, cssVar } from '../../design/tokens';
import { ENVIRONMENT, statusToken } from '../../design/semantic';
import { translateError } from '../../design/errorCopy';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { OVERLAY_KIND, releaseOverlay, requestOverlay } from './overlayRegistry';
import { TradingEnvironmentBadge } from './TradingEnvironmentBadge';

/**
 * Read at call time rather than captured at module load, matching `design/errorCopy.js`: it
 * keeps the production bundle's constant-folded `false` and lets a test drive both halves
 * with `vi.stubEnv('DEV', …)`.
 */
function isDevelopment() {
  return import.meta.env.DEV === true;
}

/** Throws in development, logs in production. The `ds/` contract-violation idiom. */
function contractError(message) {
  if (isDevelopment()) throw new Error(`ConfirmDialog: ${message}`);
  console.error(`[ds/ConfirmDialog] ${message}`);
}

/** The three intents §5.1 names. */
export const CONFIRM_INTENTS = Object.freeze(['destructive', 'live', 'neutral']);

/**
 * Intent → treatment, entirely through `design/semantic.js` and `design/tokens.js`.
 *
 * `destructive` is `status.error` and `live` is `env.live`, exactly as §5.1 specifies.
 * `neutral` is the one that does *not* come from `statusToken`, because "neutral" is not a
 * state: a calm confirmation's confirm button is the page's primary action, which is §8.3's
 * `intent="primary"` and therefore the brand token.
 */
const INTENT_TREATMENT = Object.freeze({
  destructive: Object.freeze({ accent: statusToken('error').fg, wash: statusToken('error').wash }),
  live: Object.freeze({ accent: ENVIRONMENT.LIVE.fg, wash: ENVIRONMENT.LIVE.wash }),
  neutral: Object.freeze({ accent: token.brand.base, wash: token.brand.wash }),
});

/**
 * The not-available marker (§5.1): an em-dash in `content-muted`, with the reason carried on
 * an `aria-label` so the row is not silently blank to a screen reader. Never a `0`, never an
 * empty cell, never an omitted row.
 */
const NOT_AVAILABLE = '—';

/**
 * Copy for an acknowledgement whose caller did not supply a label. It claims nothing about
 * funds or orders — inventing that sentence would be worse than a generic one — and exists
 * only so the checkbox has an accessible name (Requirement 18.4) while development is
 * throwing about the real problem.
 */
const FALLBACK_ACKNOWLEDGEMENT_LABEL = 'I understand and accept this action';

/* ══════════════════════════════════════════════════════════════════════════
 * Prop normalisation
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * One review row, or `null` when the entry cannot be rendered honestly.
 *
 * A row with no label is dropped rather than rendered, because a value with no label is not
 * information — but it is dropped *loudly* (see the caller), and the dialog still renders.
 * That asymmetry is deliberate: a dialog that throws cannot be cancelled, and refusing to
 * paint a half-configured live deployment's review is strictly worse than painting it with
 * one row missing and a console error naming the row.
 *
 * The `value` is NOT validated the same way. A missing value is a normal, expected state —
 * it is the not-available case — and is rendered, not dropped.
 *
 * @param {*} entry
 * @returns {{label: string, value: *}|null}
 */
function readReviewRow(entry) {
  if (!entry || typeof entry !== 'object') return null;
  const label = typeof entry.label === 'string' ? entry.label.trim() : '';
  if (label === '') return null;
  return { label, value: entry.value };
}

/**
 * Whether a review value is present.
 *
 * `0` and `false` are present — Requirement 14.5's whole point is that a zero is a reading
 * and not an absence, and "Reduce-only: false" is a real risk setting. Only `null`,
 * `undefined` and a blank string are absent.
 *
 * @param {*} value
 * @returns {boolean}
 */
function hasValue(value) {
  if (value === null || value === undefined) return false;
  return !(typeof value === 'string' && value.trim() === '');
}

/**
 * The acknowledgement, normalised. `null` means the caller asked for no gate.
 *
 * Note what this does *not* do: it never returns `null` for an acknowledgement that was
 * asked for and is malformed. See safety decision 4 in the header.
 *
 * @param {*} input
 * @returns {{statement: string|null, label: string|null, control: string}|null}
 */
function readAcknowledgement(input) {
  if (!input || typeof input !== 'object') return null;
  const statement =
    typeof input.statement === 'string' && input.statement.trim() !== '' ? input.statement.trim() : null;
  const label = typeof input.label === 'string' && input.label.trim() !== '' ? input.label.trim() : null;
  const raw = typeof input.control === 'string' ? input.control.trim().toLowerCase() : '';
  return { statement, label, control: raw === '' ? 'checkbox' : raw };
}

/* ══════════════════════════════════════════════════════════════════════════
 * Inline fallback for one primitive being built concurrently
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * ⚠️ SWAP WHEN TASK 6.1 LANDS ⚠️
 * ------------------------------
 * Replace with `<ErrorState error={error} context={errorContext} compact />` and delete this
 * function and the `AlertOctagon` import.
 *
 * `ds/ErrorState` (task 6.1) is being built concurrently. Until it exists this renders the
 * output of `translateError` and nothing else — never `error.message`, never a status code,
 * never a stack (Requirement 14.4). That is the same contract `ErrorState` will have, because
 * the copy comes from the same module.
 *
 * No retry affordance is rendered. §8.3's `Failed` state returns to `Review`, where the
 * confirm button is the retry — a second retry control inside the dialog would give a trader
 * two buttons that submit the same live order.
 */
function DialogError({ error, errorContext }) {
  const copy = translateError(error, errorContext);
  const treatment = statusToken('error');

  return (
    <div
      role="alert"
      data-ds="dialog-error"
      style={{
        display: 'flex',
        gap: cssVar('spacing.2'),
        padding: cssVar('spacing.3'),
        background: treatment.wash,
        border: `1px solid ${treatment.fg}`,
        borderRadius: cssVar('radius.md'),
        color: cssVar('color.content.primary'),
      }}
    >
      <AlertOctagon size={16} aria-hidden="true" style={{ color: treatment.fg, flex: '0 0 auto' }} />
      <div style={{ minWidth: 0 }}>
        <p style={{ margin: 0, fontWeight: 600, fontSize: cssVar('text.body') }}>{copy.headline}</p>
        {copy.detail ? (
          <p
            style={{
              margin: `${cssVar('spacing.1')} 0 0`,
              fontSize: cssVar('text.small'),
              color: cssVar('color.content.secondary'),
            }}
          >
            {copy.detail}
          </p>
        ) : null}
        {copy.supportRef ? (
          <p
            style={{
              margin: `${cssVar('spacing.1')} 0 0`,
              fontSize: cssVar('text.micro'),
              fontFamily: cssVar('font.mono'),
              color: cssVar('color.content.secondary'),
            }}
          >
            {`Reference ${copy.supportRef}`}
          </p>
        ) : null}
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * The component
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The one confirmation surface.
 *
 * @param {Object} props
 * @param {boolean} props.open Whether the dialog is requested. It is *requested* rather than
 *   shown: the overlay registry has the final say (Requirement 17.3), and in production a
 *   second overlay renders nothing.
 * @param {Function} props.onCancel Called by the cancel action and by Escape. Required — a
 *   confirmation with no way out is not a confirmation.
 * @param {Function} props.onConfirm Called only by an explicit activation of the confirm
 *   action, and only when the acknowledgement (if any) is satisfied and `busy` is false.
 * @param {string} props.title The dialog's accessible name, via `aria-labelledby`.
 * @param {'destructive'|'live'|'neutral'} [props.intent] `destructive` renders in
 *   `status.error`, `live` in `env.live`, `neutral` in the brand token. Default `neutral`.
 * @param {string} [props.environment] `'LIVE'|'PAPER'|'BACKTEST'`. Renders the environment
 *   badge in the header (Requirement 8.5). Omit for a confirmation that is not about
 *   trading. An unrecognised value renders `ENVIRONMENT UNCONFIRMED`, never a guess.
 * @param {string} [props.description] Body copy stating what confirming does. The primary
 *   target of `aria-describedby`.
 * @param {Array<{label: string, value: *}>} [props.review] The Requirement 8.1 review grid.
 *   A row whose `value` is `null`, `undefined` or blank renders the not-available marker.
 * @param {{statement: string, control?: 'checkbox', label: string}} [props.acknowledgement]
 *   Requirements 8.2/8.3's real-funds step. Its presence turns the gate on; confirm cannot
 *   fire until it is satisfied. Omit entirely for Paper and Backtest, so that the real-funds
 *   statement is not merely hidden but never constructed (Requirement 8.4).
 * @param {string} [props.confirmLabel] Default `'Confirm'`.
 * @param {boolean} [props.confirmDisabled] The action is not permitted *yet* — a gate the
 *   caller owns has not opened. Disables the confirm action and refuses it in the handler,
 *   the same two places the acknowledgement is enforced in, and for the same reason (safety
 *   decision 2). Cancel and Escape stay live, which is why this is not `busy`: nothing is in
 *   flight, so the trader must still be able to leave.
 *
 *   Added for `pages/Strategies.jsx`'s deploy modal, whose confirm control is gated on the
 *   Deployment_Gate preflight poll (Requirements 13.4, 13.6): a condition that passed a
 *   moment ago and has since turned red must disable the control again while the dialog
 *   stays open. This component does not evaluate the gate and holds no opinion about it —
 *   it renders the caller's verdict. The caller is expected to state the reason in the body
 *   (that page renders `DeployPreflightPanel`, one row per condition), because a disabled
 *   control is not focusable and can carry no description of its own.
 * @param {string} [props.cancelLabel] Default `'Cancel'`.
 * @param {boolean} [props.busy] A request is in flight. Both actions are disabled and
 *   Escape is inert — see the note on {@link ConfirmDialog} `busy` below.
 * @param {string} [props.busyLabel] What the confirm action reads while `busy`.
 * @param {*} [props.error] Anything catchable. Rendered through `translateError`.
 * @param {string} [props.errorContext] The `errorCopy.js` context key for that translation.
 * @param {React.ReactNode} [props.children] Extra content inside the scroll region — a
 *   labelled `Field` for the rename flow (§7.2), `DeployPreflightPanel` for §8.3's Review
 *   step, step navigation for the flow that wraps this.
 */
export function ConfirmDialog({
  open,
  onCancel,
  onConfirm,
  title,
  intent = 'neutral',
  environment,
  description,
  review,
  acknowledgement,
  confirmLabel = 'Confirm',
  confirmDisabled = false,
  cancelLabel = 'Cancel',
  busy = false,
  busyLabel = 'Working…',
  error,
  errorContext,
  children,
}) {
  const instanceId = useId();
  const titleId = `${instanceId}-title`;
  const bodyId = `${instanceId}-body`;
  const acknowledgementId = `${instanceId}-acknowledgement`;
  const acknowledgementRegionId = `${instanceId}-acknowledgement-region`;

  const dialogRef = useRef(null);
  const cancelRef = useRef(null);

  const treatment = INTENT_TREATMENT[intent] ?? INTENT_TREATMENT.neutral;
  const ack = readAcknowledgement(acknowledgement);

  /*
   * The gate's state. Reset whenever the dialog opens and whenever the statement changes,
   * keyed on the statement STRING rather than the object, so a caller passing an inline
   * object literal does not reset it on every render.
   *
   * Resetting on open is the point: a dialog that remembered a previous acknowledgement is a
   * dialog that can place a live order without an acknowledgement in this session.
   */
  const [acknowledged, setAcknowledged] = useState(false);
  const ackStatement = ack?.statement ?? null;
  useEffect(() => {
    setAcknowledged(false);
  }, [open, ackStatement]);

  /*
   * The registry has the final say on whether this dialog renders (Requirement 17.3). The
   * claim is taken in an effect and the dialog renders only once granted, so a refused
   * dialog never paints at all rather than flashing and vanishing. In development a refusal
   * throws from here; in production it returns false and this stays `null`.
   *
   * `title` is read through a ref rather than being a dependency. §8.3's flow changes the
   * title between steps, and a claim that were re-taken on every title change would unmount
   * and remount the dialog — tearing down the focus trap, handing focus back to the opener
   * and taking it again, mid-confirmation.
   */
  const labelRef = useRef(title);
  labelRef.current = title;
  const [granted, setGranted] = useState(false);
  useEffect(() => {
    if (open !== true) {
      setGranted(false);
      return undefined;
    }
    const allowed = requestOverlay({
      id: instanceId,
      kind: OVERLAY_KIND.DIALOG,
      label: labelRef.current,
    });
    setGranted(allowed);
    return () => {
      releaseOverlay(instanceId);
      setGranted(false);
    };
  }, [open, instanceId]);

  const active = open === true && granted === true;

  /*
   * ── The contract checks ────────────────────────────────────────────────
   * Collected as a pure list during render and reported from an effect, so a re-render
   * cannot log the same complaint twice and a violation cannot fire during render.
   */
  const violations = [];
  if (typeof onCancel !== 'function') {
    violations.push('`onCancel` is required — a confirmation with no way out is not a confirmation.');
  }
  if (typeof onConfirm !== 'function') {
    violations.push('`onConfirm` is required.');
  }
  if (typeof title !== 'string' || title.trim() === '') {
    violations.push("`title` is required — it is the dialog's accessible name (Requirement 18.4).");
  }
  if (!CONFIRM_INTENTS.includes(intent)) {
    violations.push(`\`intent\` must be one of ${CONFIRM_INTENTS.join(', ')}; received "${intent}".`);
  }
  if (ack !== null && ack.statement === null) {
    violations.push(
      'an `acknowledgement` was requested without a `statement`. Requirement 8.2 needs the '
        + 'explicit real-funds sentence. The gate stays on and no statement is invented.',
    );
  }
  if (ack !== null && ack.label === null) {
    violations.push(
      'an `acknowledgement` was requested without a `label`. The control needs an accessible '
        + `name (Requirement 18.4); falling back to "${FALLBACK_ACKNOWLEDGEMENT_LABEL}".`,
    );
  }
  if (ack !== null && ack.control !== 'checkbox') {
    violations.push(
      `\`acknowledgement.control\` must be "checkbox"; received "${ack.control}". Rendering a `
        + 'checkbox — the gate is never dropped because its control was misspelled.',
    );
  }

  const rows = Array.isArray(review) ? review : [];
  const reviewRows = [];
  let droppedReviewRows = 0;
  for (const entry of rows) {
    const row = readReviewRow(entry);
    if (row === null) droppedReviewRows += 1;
    else reviewRows.push(row);
  }
  if (droppedReviewRows > 0) {
    violations.push(
      `${droppedReviewRows} \`review\` row(s) have no \`label\` and were dropped. A value with no `
        + 'label is not information. The dialog still renders — refusing to paint a '
        + 'half-configured review would be worse than painting it with a row missing.',
    );
  }

  const violationsRef = useRef(violations);
  violationsRef.current = violations;
  const violationKey = violations.join('\n');
  useEffect(() => {
    if (!active || violationsRef.current.length === 0) return;
    // One error carrying every violation, so a development throw does not hide the rest.
    contractError(violationsRef.current.join(' '));
  }, [active, violationKey]);

  // ── Confirm is gated in two places. See safety decision 2. ──────────────
  // `confirmDisabled` is the caller's own gate and is ANDed in, never substituted for the
  // acknowledgement: a live deployment whose preflight has gone green still needs the box.
  const canConfirm =
    busy !== true && confirmDisabled !== true && (ack === null || acknowledged === true);

  const handleCancel = useCallback(() => {
    // `busy` means a request is already in flight. Cancelling cannot un-send it, and closing
    // the dialog would hide the outcome of a live deployment, so cancel is refused until the
    // caller resolves `busy` — by clearing `open`, or by passing `error`.
    if (busy === true) return;
    if (typeof onCancel === 'function') onCancel();
  }, [busy, onCancel]);

  const handleConfirm = useCallback(() => {
    /*
     * ═══ REQUIREMENT 8.3 — DO NOT REMOVE THIS GUARD ═══
     * The button below is also `disabled`, and that is not enough. `disabled` is a rendering;
     * this is the rule. A programmatic `.click()`, an `Enter` delivered by a stray key
     * handler, or a future refactor that styles the button instead of disabling it must not
     * be able to reach `onConfirm` before the acknowledgement.
     */
    if (busy === true) return;
    // The caller's gate, refused here as well as rendered as `disabled` — same argument.
    if (confirmDisabled === true) return;
    if (ack !== null && acknowledged !== true) return;
    if (typeof onConfirm === 'function') onConfirm();
  }, [busy, confirmDisabled, ack, acknowledged, onConfirm]);

  useFocusTrap({
    active,
    containerRef: dialogRef,
    // Requirement 8.3 / safety decision 1: cancel, never confirm.
    initialFocusRef: cancelRef,
    // Escape is inert while a request is in flight, for the reason in `handleCancel`.
    onEscape: busy === true ? undefined : handleCancel,
  });

  if (!active) return null;

  const hasBody =
    (typeof description === 'string' && description.trim() !== '')
    || reviewRows.length > 0
    || ack !== null
    || (error !== null && error !== undefined)
    || (children !== null && children !== undefined && children !== false);

  return createPortal(
    <div
      data-ds="confirm-dialog-backdrop"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: cssVar('z.modal'),
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: cssVar('spacing.4'),
        background: cssVar('color.surface.overlay'),
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={hasBody ? bodyId : undefined}
        data-ds="confirm-dialog"
        data-ds-overlay={OVERLAY_KIND.DIALOG}
        data-ds-intent={intent}
        // Holds focus when the dialog contains nothing focusable — see `useFocusTrap`'s
        // zero-focusable case. Negative, so it is not itself a tab stop.
        tabIndex={-1}
        style={{
          // ── Requirement 17.3 / P34: cannot exceed the viewport at any width ──
          maxHeight: `calc(100dvh - 2 * ${cssVar('spacing.8')})`,
          maxWidth: `min(560px, calc(100vw - 2 * ${cssVar('spacing.4')}))`,
          width: '100%',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          background: cssVar('color.surface.panel'),
          border: `1px solid ${cssVar('color.line.default')}`,
          borderTop: `2px solid ${treatment.accent}`,
          borderRadius: cssVar('radius.lg'),
          boxShadow: cssVar('shadow.overlay'),
          color: cssVar('color.content.primary'),
          fontFamily: cssVar('font.sans'),
          fontSize: cssVar('text.body'),
        }}
      >
        {/* ── Header: environment badge (Req 8.5) then title ──
            `data-ds` is forwarded onto the badge's root so the marker this dialog's
            tests query survives the swap from the inline fallback to the primitive. */}
        {environment === null || environment === undefined ? null : (
          <TradingEnvironmentBadge
            environment={environment}
            variant="strip"
            data-ds="environment-strip"
          />
        )}
        <div
          style={{
            padding: `${cssVar('spacing.4')} ${cssVar('spacing.4')} ${cssVar('spacing.3')}`,
            borderBottom: `1px solid ${cssVar('color.line.subtle')}`,
            flex: '0 0 auto',
          }}
        >
          <h2
            id={titleId}
            style={{
              margin: 0,
              fontSize: cssVar('text.section'),
              // `--text-section--line-height` carries a double dash, which `cssVar`'s
              // dot-to-dash mapping cannot express; read the generated mirror instead.
              lineHeight: token.text.lineHeight.section,
              fontWeight: 600,
              color: cssVar('color.content.primary'),
            }}
          >
            {title}
          </h2>
        </div>

        {/* ── Body: THE internal scroll region (Requirement 17.3) ── */}
        {hasBody ? (
          <div
            id={bodyId}
            data-ds="confirm-dialog-body"
            style={{
              // `min-height: 0` is what actually lets a flex child scroll rather than
              // growing past its parent and taking the dialog outside the clamp with it.
              minHeight: 0,
              overflowY: 'auto',
              padding: cssVar('spacing.4'),
              display: 'flex',
              flexDirection: 'column',
              gap: cssVar('spacing.4'),
            }}
          >
            {typeof description === 'string' && description.trim() !== '' ? (
              <p style={{ margin: 0, color: cssVar('color.content.secondary') }}>{description}</p>
            ) : null}

            {reviewRows.length > 0 ? (
              <dl
                data-ds="confirm-dialog-review"
                style={{
                  margin: 0,
                  display: 'grid',
                  gridTemplateColumns: 'minmax(0, 12rem) minmax(0, 1fr)',
                  gap: `${cssVar('spacing.2')} ${cssVar('spacing.4')}`,
                }}
              >
                {reviewRows.map((row, index) => (
                  // The label is not unique by construction — two rows may share one — so
                  // the index is part of the key.
                  <div key={`${index}-${row.label}`} style={{ display: 'contents' }}>
                    <dt
                      style={{
                        margin: 0,
                        color: cssVar('color.content.secondary'),
                        fontSize: cssVar('text.small'),
                      }}
                    >
                      {row.label}
                    </dt>
                    {hasValue(row.value) ? (
                      <dd
                        style={{
                          margin: 0,
                          color: cssVar('color.content.primary'),
                          fontFamily: cssVar('font.mono'),
                          fontVariantNumeric: 'tabular-nums',
                          overflowWrap: 'anywhere',
                        }}
                      >
                        {row.value}
                      </dd>
                    ) : (
                      /*
                       * Safety decision 3. Never omitted, never defaulted, never a `0`.
                       * `content-muted` is annotated NON-TEXT ONLY at 3.2:1 in tokens.css and
                       * is valid here precisely because the marker always sits beside its
                       * label and carries its own accessible name.
                       */
                      <dd
                        style={{ margin: 0, color: cssVar('color.content.muted') }}
                        aria-label={`${row.label}: not available`}
                        data-ds="not-available"
                      >
                        {NOT_AVAILABLE}
                      </dd>
                    )}
                  </div>
                ))}
              </dl>
            ) : null}

            {children}

            {/* ── Requirements 8.2 / 8.3: the real-funds step ── */}
            {ack !== null ? (
              <div
                id={acknowledgementRegionId}
                data-ds="confirm-dialog-acknowledgement"
                style={{
                  padding: cssVar('spacing.3'),
                  background: treatment.wash,
                  border: `1px solid ${treatment.accent}`,
                  borderRadius: cssVar('radius.md'),
                  display: 'flex',
                  flexDirection: 'column',
                  gap: cssVar('spacing.2'),
                }}
              >
                {ack.statement === null ? null : (
                  <p style={{ margin: 0, fontWeight: 600 }}>{ack.statement}</p>
                )}
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: cssVar('spacing.2') }}>
                  <input
                    id={acknowledgementId}
                    type="checkbox"
                    checked={acknowledged}
                    disabled={busy === true}
                    onChange={(event) => setAcknowledged(event.target.checked === true)}
                    style={{ marginTop: '0.15rem', accentColor: treatment.accent, flex: '0 0 auto' }}
                  />
                  <label htmlFor={acknowledgementId} style={{ color: cssVar('color.content.primary') }}>
                    {ack.label ?? FALLBACK_ACKNOWLEDGEMENT_LABEL}
                  </label>
                </div>
              </div>
            ) : null}

            {error === null || error === undefined ? null : (
              <DialogError error={error} errorContext={errorContext} />
            )}
          </div>
        ) : null}

        {/* ── Footer. Cancel is FIRST in the DOM, so tab order and visual order agree. ── */}
        <div
          style={{
            flex: '0 0 auto',
            display: 'flex',
            justifyContent: 'flex-end',
            gap: cssVar('spacing.2'),
            padding: cssVar('spacing.4'),
            borderTop: `1px solid ${cssVar('color.line.subtle')}`,
            background: cssVar('color.surface.panel'),
          }}
        >
          <button
            ref={cancelRef}
            type="button"
            onClick={handleCancel}
            disabled={busy === true}
            data-ds="confirm-dialog-cancel"
            style={{
              padding: `${cssVar('spacing.2')} ${cssVar('spacing.4')}`,
              background: 'transparent',
              border: `1px solid ${cssVar('color.line.strong')}`,
              borderRadius: cssVar('radius.sm'),
              color: cssVar('color.content.primary'),
              font: 'inherit',
              cursor: busy === true ? 'not-allowed' : 'pointer',
              opacity: busy === true ? 0.6 : 1,
            }}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={!canConfirm}
            // Points at the acknowledgement region, not the checkbox, so focus landing on
            // confirm announces the real-funds statement and the gate together.
            aria-describedby={ack !== null ? acknowledgementRegionId : undefined}
            data-ds="confirm-dialog-confirm"
            style={{
              padding: `${cssVar('spacing.2')} ${cssVar('spacing.4')}`,
              background: treatment.wash,
              border: `1px solid ${treatment.accent}`,
              borderRadius: cssVar('radius.sm'),
              color: treatment.accent,
              font: 'inherit',
              fontWeight: 600,
              cursor: canConfirm ? 'pointer' : 'not-allowed',
              opacity: canConfirm ? 1 : 0.6,
            }}
          >
            {busy === true ? busyLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default ConfirmDialog;
