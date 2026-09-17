/**
 * ═══════════════════════════════════════════════════════════════════════════
 * builder/validationSurfaces — §9.3's four surfaces, on two axes
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 24.4b (groundwork for 24.4). design.md §9.3, §9.4.
 * Requirements 5.4, 5.5, 1.5. Property P10.
 *
 * ═══ WHAT IT REPLACES ═══
 *
 * Five different meanings render as ONE treatment on the builder today — a `C.gold`
 * band with `fontFamily: monospace`, 8px/16px padding and a 1px bottom rule:
 * `validation-unavailable`, `canvas-notice`, `deployed-lock`, `subscription-refusals`
 * and `training-blocks`. `local-advisory`, `serializer-error` and `save-issues` are the
 * same band in `C.red`. A trader reading the top of the page cannot tell "you are
 * mid-action and this drop was refused" from "the saved graph will not run", because
 * both are the same amber monospace strip.
 *
 * Requirement 5.5 asks for those meanings to be *distinguishable*. That is what this
 * module is: the surfaces spelled once, so the builder composes them rather than
 * repainting a band per call site.
 *
 * ═══ THE TWO AXES ARE INDEPENDENT, AND THAT IS THE DESIGN ═══
 *
 * **Axis 1 — severity.** WHAT KIND of thing is being said. §9.3's table:
 *
 *   | surface       | token             | border | icon          | role     |
 *   | ------------- | ----------------- | ------ | ------------- | -------- |
 *   | `guidance`    | `status.guidance` | dashed | Info          | `status` |
 *   | `warning`     | `status.warning`  | solid  | AlertTriangle | `status` |
 *   | `error`       | `status.error`    | solid  | AlertOctagon  | `alert`  |
 *   | `destructive` | `status.error`    | solid  | Trash2        | `dialog` |
 *
 * **Axis 2 — provenance.** WHO SAID IT. `local` is a client-side verdict that the
 * backend has not seen; `confirmed` is the server's own. `lib/connectionLegality.js`
 * marks every local verdict `provisional: true / authority: 'client-provisional'` and
 * `reconcileWithBackend()` lets the backend override it, so the distinction is real in
 * the data and not a presentation flourish.
 *
 * **They are separate props and neither is derivable from the other.** A local *error*
 * is the builder's most common banner (`local-advisory`: "3 errors: … (local check — the
 * backend has not validated this version yet)"), and a *confirmed* guidance note is
 * equally expressible. Collapsing them into one five-value enum would make one of those
 * two unsayable, and would put the live-region role — the thing that decides whether a
 * screen reader is interrupted — behind a question about *authority* rather than about
 * severity. Guidance never earns `role="alert"`: an author who is mid-drag has nothing
 * wrong with their strategy, and cutting them off to say so is the noise Requirement
 * 16.2 forbids.
 *
 * The provenance axis shows up in TWO channels at once, from one prop:
 *
 *   * the **left rail** — dashed for `local`, solid for `confirmed` (§9.3 point 4);
 *   * the **sentence** — {@link LOCAL_CHECK_NOTE}, appended verbatim on `local`.
 *
 * The sentence is not optional and there is no prop to suppress it. It is the only thing
 * telling an author that a verdict on screen is not authoritative, and a rail alone is
 * colour-and-shape — unreadable to a screen reader and unreadable in a screenshot. One
 * prop drives both, so a local verdict cannot be shipped looking authoritative.
 *
 * ═══ COMPOSED, NOT HAND-ROLLED ═══
 *
 * `ds/Alert` already carries §9.3's middle three rows (see its `SEVERITY` table) and
 * already derives the hue, the icon, the border style and the live-region role from
 * `severity` alone. So the three banner surfaces ARE `ds/Alert`, and the destructive one
 * IS `ds/ConfirmDialog` — this module chooses between them and adds the second axis.
 *
 * Nothing here declares a colour. {@link statusToken} resolves the rail's hue from the
 * SAME state key handed to `ds/Alert`'s `severity`, so the rail cannot end up a
 * different colour from the band it is attached to even if `tokens.css` splits the
 * `warning` / `guidance` values later.
 *
 * The rail lives on a WRAPPER around the `ds/Alert`, not on the alert's own border, for
 * two reasons. `ds/Alert` sets `borderStyle` inline from its severity, so a `dashed`
 * guidance band and a `solid` provenance rail would be one property fighting itself.
 * And `ds/Alert` spreads `...rest` after its own `style`, so a `style` passed at a call
 * site silently erases its background and border colour — this module passes it none.
 *
 * @module components/builder/validationSurfaces
 */

import { AlertOctagon, AlertTriangle, Info, Lock, Trash2 } from 'lucide-react';

import { statusToken } from '../../design/semantic';
import { Alert } from '../ds/Alert';
import { ConfirmDialog } from '../ds/ConfirmDialog';
import { assertContract, hasText } from '../ds/devAssert';

/* ══════════════════════════════════════════════════════════════════════════
 * Axis 1 — severity
 * ══════════════════════════════════════════════════════════════════════════ */

/** The four surfaces, by id. §9.3's rows, in escalating order. */
export const SURFACE = Object.freeze({
  GUIDANCE: 'guidance',
  WARNING: 'warning',
  ERROR: 'error',
  DESTRUCTIVE: 'destructive',
});

/** The four ids as a list, for exhaustive tests and for P10's sweep. */
export const VALIDATION_SURFACES = Object.freeze(Object.values(SURFACE));

/**
 * surface → the whole §9.3 row.
 *
 * `severity` is the string handed to `ds/Alert`, and `null` for `destructive` because a
 * destructive action is not a band: it is a dialog, and a `ds/Alert` that offered to
 * render one would be the fifth gold banner this module exists to remove.
 *
 * `tokenState` is the key handed to {@link statusToken}. It is spelled here rather than
 * assumed equal to `severity` because the destructive row shares `status.error` with the
 * error row while being a different surface — the two axes of §9.3's table are token AND
 * icon, and only the pair is distinct.
 */
const TREATMENT = Object.freeze({
  [SURFACE.GUIDANCE]: Object.freeze({
    id: SURFACE.GUIDANCE,
    severity: 'guidance',
    tokenState: 'guidance',
    role: 'status',
    border: 'dashed',
    Icon: Info,
  }),
  [SURFACE.WARNING]: Object.freeze({
    id: SURFACE.WARNING,
    severity: 'warning',
    tokenState: 'warning',
    role: 'status',
    border: 'solid',
    Icon: AlertTriangle,
  }),
  [SURFACE.ERROR]: Object.freeze({
    id: SURFACE.ERROR,
    severity: 'error',
    tokenState: 'error',
    role: 'alert',
    border: 'solid',
    Icon: AlertOctagon,
  }),
  [SURFACE.DESTRUCTIVE]: Object.freeze({
    id: SURFACE.DESTRUCTIVE,
    severity: null,
    tokenState: 'error',
    role: 'dialog',
    border: 'solid',
    Icon: Trash2,
  }),
});

/**
 * The surface an unrecognised id renders as.
 *
 * `warning` for the reason `ds/Alert` gives: understating would announce a real failure
 * politely, and overstating would interrupt a screen reader for a routine one. It is
 * also the only fallback that cannot turn a mistyped guidance id into `role="alert"`.
 */
const FALLBACK_SURFACE = SURFACE.WARNING;

/**
 * The §9.3 row for a surface. Total: anything unrecognised answers for
 * {@link FALLBACK_SURFACE}, so no caller has to guard the result.
 *
 * @param {unknown} surface
 * @returns {{id: string, severity: string|null, tokenState: string, role: string, border: string, Icon: Function}}
 */
export function surfaceTreatment(surface) {
  const key = typeof surface === 'string' ? surface.trim().toLowerCase() : '';
  return Object.prototype.hasOwnProperty.call(TREATMENT, key)
    ? TREATMENT[key]
    : TREATMENT[FALLBACK_SURFACE];
}

/**
 * The `ds/Alert` severity a surface renders as, or `null` for `destructive`.
 *
 * Exported so the repoint — and P10 — can assert the mapping without rendering, and so
 * that "the invalid-connection surface resolves to the guidance token and never the
 * destructive or error token" is a statement about a function.
 *
 * @param {unknown} surface
 * @returns {string|null}
 */
export function surfaceSeverity(surface) {
  return surfaceTreatment(surface).severity;
}

/**
 * The live-region role a surface earns: `'status'`, `'alert'` or `'dialog'`.
 *
 * `'dialog'` is not a live region at all, which is the point — a destructive action asks
 * a question and waits, rather than announcing something.
 *
 * @param {unknown} surface
 * @returns {'status'|'alert'|'dialog'}
 */
export function surfaceRole(surface) {
  return surfaceTreatment(surface).role;
}

/**
 * The glyph a surface carries. The non-colour axis: three of the five hues in
 * `tokens.css` are shared, so hue alone cannot separate four surfaces.
 *
 * @param {unknown} surface
 * @returns {Function} A lucide component.
 */
export function surfaceIcon(surface) {
  return surfaceTreatment(surface).Icon;
}

/* ══════════════════════════════════════════════════════════════════════════
 * Axis 2 — provenance
 * ══════════════════════════════════════════════════════════════════════════ */

/** Who authored the verdict. */
export const PROVENANCE = Object.freeze({
  /** A client-side check. `connectionLegality.js`'s `authority: 'client-provisional'`. */
  LOCAL: 'local',
  /** The server's own verdict — a validation report, a canvas state, a save response. */
  CONFIRMED: 'confirmed',
});

/** The two values as a list. */
export const VALIDATION_PROVENANCES = Object.freeze(Object.values(PROVENANCE));

/**
 * The provisional-verdict sentence, **verbatim** from the banner it comes from.
 *
 * It is the only thing on screen telling an author that a verdict is not the backend's.
 * Reworded, it stops being that: "not yet validated" and "not validated" are different
 * claims, and a trader who reads the second one goes looking for a save that already
 * happened. Kept as one exported constant so the wording exists once in the tree.
 */
export const LOCAL_CHECK_NOTE = '(local check — the backend has not validated this version yet)';

/**
 * The provenance an unrecognised value renders as.
 *
 * `local` — and the direction matters. Understating authority shows a dashed rail and a
 * "the backend has not validated this" note on a verdict the backend did in fact give:
 * mildly redundant. Overstating it presents a guess as the server's answer. Only one of
 * those two can cost a trader a deployment.
 */
const FALLBACK_PROVENANCE = PROVENANCE.LOCAL;

/**
 * The left rail's border style for a provenance: `'dashed'` local, `'solid'` confirmed.
 *
 * Total over every input; see {@link FALLBACK_PROVENANCE} for why the fallback is the
 * dashed one.
 *
 * @param {unknown} provenance
 * @returns {'dashed'|'solid'}
 */
export function provenanceRail(provenance) {
  const key = typeof provenance === 'string' ? provenance.trim().toLowerCase() : '';
  return key === PROVENANCE.CONFIRMED ? 'solid' : 'dashed';
}

/**
 * The sentence a provenance adds, or `''` when it adds none.
 *
 * A function rather than inline JSX so the wording is assertable without a DOM, the same
 * reason `connectionRefusalLines` is one.
 *
 * @param {unknown} provenance
 * @returns {string} {@link LOCAL_CHECK_NOTE} or `''`.
 */
export function localCheckNote(provenance) {
  return provenanceRail(provenance) === 'dashed' ? LOCAL_CHECK_NOTE : '';
}

/**
 * The normalised provenance id, for `data-provenance`.
 *
 * Kept separate from {@link provenanceRail} on purpose: the rail is total over two
 * values, and this reports which of the two declared ids was actually recognised, so the
 * DOM records the decision rather than only its rendering.
 */
function readProvenance(provenance) {
  const key = typeof provenance === 'string' ? provenance.trim().toLowerCase() : '';
  return VALIDATION_PROVENANCES.includes(key) ? key : FALLBACK_PROVENANCE;
}

/**
 * What the lock says when the server sends no sentence. Byte-for-byte the fallback the
 * current banner carries — it is not new copy, and it claims only what `canvas_state`
 * having reported a lock already implies.
 */
export const DEPLOYED_LOCK_FALLBACK_REASON =
  'This version is deployed, so the canvas is read-only.';

/* ══════════════════════════════════════════════════════════════════════════
 * The banner surfaces
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * One validation band: a `ds/Alert` on the severity axis, inside a provenance rail.
 *
 * @param {Object} props
 * @param {'guidance'|'warning'|'error'} props.surface REQUIRED. §9.3's row. `destructive`
 *   is refused here and renders nothing — it is {@link DestructiveConfirm}'s, and a
 *   destructive action rendered as a band is exactly the confusion Requirement 5.5 is
 *   about.
 * @param {'local'|'confirmed'} props.provenance REQUIRED, with no default. Neither
 *   default is safe: `confirmed` would present an unvalidated guess as the server's
 *   answer, and `local` would tell an author the backend has not seen a verdict it
 *   authored. Development throws; production falls back to `local`.
 * @param {string} props.title REQUIRED. The condition, in one line — `ds/Alert`
 *   announces it, so it states the condition rather than naming it.
 * @param {React.ReactNode} [props.children] The detail, when the title does not carry it.
 * @param {Function} [props.glyph] An extra lucide component for the band's SUBJECT, not
 *   its severity — §9.3's `Lock` on the deployed-lock notice is the one case, replacing
 *   an emoji that was never a severity marker. It renders inline at the head of the body
 *   and changes no hue, no border and no role; the severity glyph stays `ds/Alert`'s, so
 *   two surfaces can never come to share one icon.
 * @param {'block'|'strip'} [props.variant] Default `'strip'`: every band this replaces is
 *   full-width under the header. The in-panel notes pass `'block'`.
 * @param {string} [props.className]
 */
export function ValidationSurface({
  surface,
  provenance,
  title,
  children,
  glyph: Glyph,
  variant = 'strip',
  className = '',
  ...rest
}) {
  const key = typeof surface === 'string' ? surface.trim().toLowerCase() : '';
  assertContract(
    VALIDATION_SURFACES.includes(key),
    `ValidationSurface: \`surface\` must be one of ${VALIDATION_SURFACES.join(' | ')}, `
      + `received ${JSON.stringify(surface)}. Rendering as \`${FALLBACK_SURFACE}\`.`,
  );

  const treatment = surfaceTreatment(key);

  // The destructive row is a dialog, and a band that offered to be one would let a
  // delete look like a notice. Refused rather than downgraded to `error`: silently
  // rendering a destructive action as a red strip is the failure this module removes.
  if (
    !assertContract(
      treatment.id !== SURFACE.DESTRUCTIVE,
      'ValidationSurface: the destructive surface is a `ds/ConfirmDialog`, not a band — use '
        + '`DestructiveConfirm`. §9.3 gives it `role="dialog"`, because a destructive action '
        + 'asks a question and waits rather than announcing something.',
    )
  ) {
    return null;
  }

  assertContract(
    VALIDATION_PROVENANCES.includes(
      typeof provenance === 'string' ? provenance.trim().toLowerCase() : '',
    ),
    'ValidationSurface: `provenance` is required and must be one of '
      + `${VALIDATION_PROVENANCES.join(' | ')}, received ${JSON.stringify(provenance)}. `
      + `Rendering as \`${FALLBACK_PROVENANCE}\` — a verdict shown as the backend's when it `
      + 'is not is the one error here that can cost a deployment (Requirement 5.5).',
  );

  const resolvedProvenance = readProvenance(provenance);
  const rail = provenanceRail(resolvedProvenance);
  const note = localCheckNote(resolvedProvenance);
  const { fg } = statusToken(treatment.tokenState);

  const body =
    Glyph || (children !== null && children !== undefined && children !== false) || note !== ''
      ? (
        <>
          {Glyph ? (
            <Glyph
              size={12}
              strokeWidth={2}
              aria-hidden="true"
              data-ds="validation-surface-glyph"
              className="mr-1 inline-block shrink-0 align-middle"
              style={{ color: fg }}
            />
          ) : null}
          {children}
          {note === '' ? null : (
            <span data-ds="validation-provenance-note" className="ml-1">
              {note}
            </span>
          )}
        </>
      )
      : null;

  return (
    <div
      data-ds="validation-surface"
      data-surface={treatment.id}
      data-provenance={resolvedProvenance}
      data-rail={rail}
      className={`w-full ${className}`.trim()}
      // The rail, and only the rail. `border-l-2` gives the left edge a width and leaves
      // the other three at zero, so `borderStyle` here cannot reach any other edge and
      // cannot reach the `ds/Alert` inside either.
      style={{ borderLeftWidth: '2px', borderLeftStyle: rail, borderLeftColor: fg }}
    >
      <Alert severity={treatment.severity} title={title} variant={variant} {...rest}>
        {body}
      </Alert>
    </div>
  );
}

/**
 * The deployed-version lock (§9.4, Requirement 9.9), on the warning surface.
 *
 * WHY WARNING AND NOT GUIDANCE
 * ----------------------------
 * The notice reads *"This version is deployed, so the canvas is read-only."* Nothing the
 * author is part-way through caused it and nothing they can do clears it: it is a
 * standing fact about the SAVED version, published by the backend as task 8.3's
 * `canvas_state`, and migration 004c's immutability trigger will refuse an edit that
 * gets past the canvas anyway. Guidance means "not yet, keep going"; this is "not here".
 * §9.3 puts it on the warning surface and the copy agrees.
 *
 * WHY `confirmed`
 * --------------
 * The verdict, the sentence and the frozen-field list are all the server's. This banner
 * formats nothing and decides nothing, so its rail is solid — the canvas cannot claim a
 * lock the backend did not publish, and cannot deny one it did.
 *
 * WHY THE `Lock` GLYPH IS A SUBJECT GLYPH
 * ---------------------------------------
 * The `🔒` it replaces sat `aria-hidden` beside the sentence in a band that had no other
 * icon, so it was never carrying severity — it was naming the subject. It comes back as
 * a real `Lock` on the {@link ValidationSurface} `glyph` slot, and `ds/Alert` keeps
 * `AlertTriangle` for the severity. Two glyphs saying two different things, rather than
 * one glyph overloaded, and no emoji: an emoji's rendering is font-dependent and its
 * accessible name is whatever the platform decides.
 *
 * @param {Object} props
 * @param {string} [props.reason] The server's sentence. Absent falls back to
 *   {@link DEPLOYED_LOCK_FALLBACK_REASON} — the same fallback the banner uses today.
 * @param {Array<string>} [props.frozenFields] The fields the deployment freezes, as the
 *   server names them. Rendered when non-empty; today they reach the DOM only as a data
 *   attribute, so an author cannot read which fields are frozen without dev tools.
 */
export function DeployedLockNotice({ reason, frozenFields, ...rest }) {
  const fields = Array.isArray(frozenFields) ? frozenFields.filter(hasText) : [];
  return (
    <ValidationSurface
      surface={SURFACE.WARNING}
      provenance={PROVENANCE.CONFIRMED}
      title={hasText(reason) ? reason : DEPLOYED_LOCK_FALLBACK_REASON}
      glyph={Lock}
      data-frozen-fields={fields.join(' ')}
      {...rest}
    >
      {fields.length > 0 ? (
        <span data-ds="deployed-lock-frozen-fields">{`Frozen fields: ${fields.join(', ')}`}</span>
      ) : null}
    </ValidationSurface>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * The destructive surface
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * A destructive confirmation: `ds/ConfirmDialog` at `intent="destructive"`, with §9.3's
 * `Trash2` and **no acknowledgement checkbox**.
 *
 * ═══ WHY NO CHECKBOX, AND WHAT THIS IS AND IS NOT FOR ═══
 *
 * `ds/ConfirmDialog`'s acknowledgement is Requirement 8.2's real-funds statement. It is
 * spent deliberately and rarely: a gate that appears on everything is a gate a trader
 * learns to tick without reading, and the one place that must never happen is the live
 * deployment dialog. So this wrapper REFUSES an `acknowledgement` rather than passing it
 * through. A caller that genuinely needs one — a bulk irreversible action — composes
 * `ds/ConfirmDialog` directly and says why there.
 *
 * The gate that remains is the dialog itself, and it is not nothing: initial focus is on
 * cancel (`ds/ConfirmDialog` safety decision 1), so a trader who opens it and hits Enter
 * out of habit lands on "no".
 *
 * ═══ NOT FOR DELETING A NODE ═══
 *
 * §9.3's destructive row names "delete node, delete strategy" together, and they are not
 * alike. Deleting a node mutates local React state, pushes the result onto the builder's
 * undo stack and reaches no endpoint; the saved version on the server is immutable and
 * untouched until the author presses Save. Its correct treatment is the destructive
 * TREATMENT — `ds/CommandButton intent="destructive"` with `Icon={Trash2}`, which takes
 * `status.error` from `design/semantic.js` and accepts no colour prop — and no dialog.
 * A modal on a reversible local edit is friction that trains the reflex the live-deploy
 * dialog depends on not existing.
 *
 * This component is for the destructive actions that leave the tab.
 *
 * @param {Object} props
 * @param {boolean} props.open
 * @param {Function} props.onCancel
 * @param {Function} props.onConfirm
 * @param {string} props.title The dialog's accessible name.
 * @param {string} [props.statement] What confirming destroys, in one line. Rendered
 *   beside the `Trash2` inside the body rather than through `ds/ConfirmDialog`'s
 *   `description`, so the glyph sits at the head of the body instead of below the review
 *   grid.
 * @param {Array<{label: string, value: *}>} [props.review] Rows for the dialog's review
 *   grid. A value the caller cannot supply renders the not-available marker; it is never
 *   defaulted.
 * @param {string} [props.confirmLabel] Default `'Delete'`. Name the act, not "OK".
 * @param {string} [props.cancelLabel] Default `'Cancel'`.
 * @param {boolean} [props.busy]
 * @param {string} [props.busyLabel]
 * @param {*} [props.error] Anything catchable; the dialog renders it through
 *   `translateError`.
 * @param {string} [props.errorContext]
 * @param {React.ReactNode} [props.children]
 */
export function DestructiveConfirm({
  open,
  onCancel,
  onConfirm,
  title,
  statement,
  review,
  confirmLabel = 'Delete',
  cancelLabel = 'Cancel',
  busy,
  busyLabel,
  error,
  errorContext,
  children,
  ...rest
}) {
  assertContract(
    !Object.prototype.hasOwnProperty.call(rest, 'acknowledgement'),
    'DestructiveConfirm: an `acknowledgement` was passed and is refused. The checkbox is '
      + "Requirement 8.2's real-funds gate and is spent on bulk irreversible actions, not on "
      + 'every destructive one — a gate on everything is a gate that gets ticked unread. Use '
      + '`ds/ConfirmDialog` directly and state the reason there.',
  );
  delete rest.acknowledgement;

  // Refused, not overridden: `intent` is what makes the dialog's accent `status.error`,
  // and §9.3 gives the destructive surface exactly one token.
  assertContract(
    !Object.prototype.hasOwnProperty.call(rest, 'intent'),
    'DestructiveConfirm: `intent` is fixed at `destructive` (§9.3). Pass `ds/ConfirmDialog` '
      + 'directly for any other intent.',
  );
  delete rest.intent;

  const { fg } = statusToken(surfaceTreatment(SURFACE.DESTRUCTIVE).tokenState);
  const Icon = surfaceIcon(SURFACE.DESTRUCTIVE);

  return (
    <ConfirmDialog
      open={open}
      onCancel={onCancel}
      onConfirm={onConfirm}
      title={title}
      intent="destructive"
      review={review}
      confirmLabel={confirmLabel}
      cancelLabel={cancelLabel}
      busy={busy}
      busyLabel={busyLabel}
      error={error}
      errorContext={errorContext}
      {...rest}
    >
      <div data-ds="destructive-statement" className="flex items-start gap-2">
        <Icon
          size={14}
          strokeWidth={2}
          aria-hidden="true"
          className="mt-0.5 shrink-0"
          style={{ color: fg }}
        />
        {hasText(statement) ? <p className="m-0 min-w-0">{statement}</p> : null}
      </div>
      {children}
    </ConfirmDialog>
  );
}
