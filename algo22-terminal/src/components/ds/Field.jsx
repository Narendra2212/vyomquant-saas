/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/Field — the only way a form control is rendered on an in-scope page
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.15. design.md §11.2. Requirements 15.1, 15.2, 15.3.
 *
 * THE THING THIS REPLACES
 * ----------------------
 * `Inp` in `ui-legacy/primitives.jsx` ends its input element with
 *
 *     aria-label={ariaLabel || lbl || ph}
 *
 * so a call site that passes only `ph` gets an accessible name made of the
 * placeholder. That reads plausibly — a screen reader says "100000.00" — and it is
 * exactly what Requirement 15.1 forbids: the label must be "distinct from and in
 * addition to any placeholder". A placeholder is also erased the moment the trader
 * types, so the sighted version of that field loses its name at precisely the point
 * the trader is deciding what to put in it. `Field` has no such fallback and no
 * hidden-label escape hatch: `label` is required, it renders as a visible
 * `<label htmlFor>`, and there is no prop that will make it disappear.
 *
 * THREE CONTRACTS IT WILL NOT RENDER WITHOUT
 * ----------------------------------------
 *   1. `label` — Requirement 15.1. Missing, blank, or non-string is a development
 *      failure. The placeholder is never promoted in its place.
 *   2. `disabled && !disabledReason` — Requirement 15.3. A greyed control with no
 *      stated reason is the trader's dead end: nothing on screen says whether they
 *      lack a permission, need to stop a session first, or are looking at a bug.
 *      The reason renders as visible help text *and* joins the accessible
 *      description, so it is there whether the trader is reading or listening.
 *   3. `invalid && !error` — Requirement 15.2. The requirement asks for a message
 *      "identifying the specific field and the reason it is invalid", so an error
 *      *treatment* with no message satisfies neither half. Whenever this component
 *      sets `aria-invalid`, a message exists and is linked by `aria-describedby`;
 *      the two cannot come apart.
 *
 * WHEN THE MESSAGE APPEARS (design.md §11.2)
 * -----------------------------------------
 * On blur and on submit — not per keystroke. A trader typing `100000` passes
 * through `1`, `10`, `100` … and a form that validates every one of those tells
 * them four times that their capital is below the minimum before they have finished
 * saying what it is. So the page computes `error` as a pure function of the value on
 * every render (that is what `parseCapitalToMinor` is for) and `Field` owns the
 * *display* gate:
 *
 *   * hidden while the field has never been blurred;
 *   * hidden again as soon as the trader edits, because they are already fixing it;
 *   * shown on blur;
 *   * shown unconditionally once the page passes `submitted` — Requirement 15.2's
 *     trigger is the submit, and the message must survive the trader poking at the
 *     field afterwards.
 *
 * `aria-invalid` follows the same gate. An input marked invalid with no message on
 * screen is worse than an unmarked one.
 *
 * The message is NOT a live region. Nine fields failing at once would announce nine
 * times over each other, and `aria-describedby` already reads the message when focus
 * reaches the field. A form submitting with errors should move focus to the first
 * invalid field — that is the page's job, and `data-field-invalid` is on the wrapper
 * so it can find it without knowing the field ids.
 *
 * NUMBERS ARE STRINGS UNTIL SOMEONE PARSES THEM
 * --------------------------------------------
 * `type="number"` is not used, deliberately. It lets the browser normalise, reformat
 * and silently blank the value, `valueAsNumber` rounds, and a scroll wheel over a
 * focused field changes a trader's capital. `type="number"` and `type="decimal"`
 * both render `<input type="text" inputMode="decimal">`, which raises the numeric
 * keypad on a tablet and hands the value back byte-for-byte as typed.
 *
 * Nothing here coerces: `onChange` receives the DOM event and
 * `event.target.value` is the exact string. `paperTradingFormat.js`'s
 * `parseCapitalToMinor` is the model — it concatenates digits rather than
 * multiplying, because `19.99 * 100` is `1998.9999999999998`, and it *refuses* a
 * value with too many decimal places rather than rounding one into a balance. A
 * `Field` that pre-rounded on the way in would make that refusal unreachable.
 *
 * @module components/ds/Field
 */

import { useId, useState } from 'react';

import { useDeclaredAdvancedFields } from './Accordion';
import { assertContract, hasText } from './devAssert';

/**
 * Marks the wrapper of every rendered field. A page looking for the first invalid
 * control to focus after a failed submit reads `[data-field-invalid="true"]`, and
 * P28/P31's DOM sweeps enumerate fields by this attribute rather than by guessing at
 * class names.
 */
export const FIELD_ATTR = 'data-field-id';

/**
 * The `type` values that mean "a number the trader typed", all of which render as
 * text with a decimal keypad. See the module note above for why `type="number"`
 * never reaches the DOM.
 */
export const NUMERIC_TYPES = Object.freeze(['number', 'decimal', 'currency']);

/**
 * Map a `Field` `type` onto the DOM `type` / `inputMode` pair it renders.
 *
 * Exported because "numeric fields use `inputMode="decimal"`" is a claim about this
 * function, and a test asserting it should read the function rather than re-derive
 * the mapping.
 *
 * @param {string} [type]
 * @returns {{type: string, inputMode: string|undefined}}
 */
export function resolveInputType(type = 'text') {
  const key = typeof type === 'string' ? type.trim().toLowerCase() : 'text';
  if (NUMERIC_TYPES.includes(key)) return { type: 'text', inputMode: 'decimal' };
  return { type: key === '' ? 'text' : key, inputMode: undefined };
}

/**
 * Should the validation message be on screen this render?
 *
 * Split out from the component so the timing rule is one testable expression rather
 * than a condition buried in JSX. See "WHEN THE MESSAGE APPEARS" above.
 *
 * @param {Object} state
 * @param {boolean} state.hasError A message exists to show.
 * @param {boolean} state.submitted The page has attempted a submit.
 * @param {boolean} state.blurred The trader has left the field at least once.
 * @param {boolean} state.editing The trader has typed since that blur.
 * @returns {boolean}
 */
export function shouldShowError({ hasError, submitted, blurred, editing }) {
  if (!hasError) return false;
  if (submitted) return true;
  return blurred && !editing;
}

/** Shared control chrome. Colour, radius and type size are all token utilities. */
const CONTROL_BASE =
  'w-full rounded-sm border bg-surface-inset px-3 py-2 text-body text-content-primary '
  + 'transition-colors placeholder:text-content-muted';

const CONTROL_RESTING = 'border-line-default hover:border-line-strong';
const CONTROL_INVALID = 'border-status-error bg-status-error-wash';
const CONTROL_DISABLED = 'border-line-subtle text-content-secondary cursor-not-allowed';

/**
 * A labelled form control.
 *
 * @param {Object} props
 * @param {string} [props.id] Explicit DOM id. Generated from `useId()` when absent —
 *   never derived from the label, which `Inp` did and which collides the moment two
 *   forms on a page both say "Amount".
 * @param {string} props.label REQUIRED, visible, and never the placeholder (Req 15.1).
 * @param {string} [props.placeholder] An example of the format. Optional, and it is
 *   not a label.
 * @param {string} [props.hint] Standing help text, always visible.
 * @param {string|number} [props.value] Controlled value. Kept as the exact string typed.
 * @param {Function} [props.onChange] Receives the DOM event; `event.target.value` is
 *   untouched.
 * @param {Function} [props.onBlur] Called after the internal blur bookkeeping.
 * @param {boolean} [props.invalid] Error treatment. Requires `error` (Req 15.2).
 * @param {string} [props.error] The message. Displayed on blur and on submit.
 * @param {boolean} [props.submitted] The page has attempted a submit — show `error` now.
 * @param {boolean} [props.disabled] Requires `disabledReason` (Req 15.3).
 * @param {string} [props.disabledReason] REQUIRED when `disabled`. Visible *and* in
 *   the accessible description.
 * @param {boolean} [props.required] Sets `required`/`aria-required` and shows the word.
 * @param {string} [props.unit] A currency or unit suffix, e.g. `USD`, `bps`. In the
 *   accessible description, because a sighted trader reads it beside the input.
 * @param {string} [props.type] `'text' | 'search' | 'date' | 'number' | 'decimal' | …`
 * @param {Array<{value: string, label: string}>} [props.options] Present and non-empty
 *   renders a `<select>` instead of an `<input>`.
 * @param {string} [props.className] Applied to the wrapper, for width and layout.
 */
export function Field({
  id,
  label,
  placeholder,
  hint,
  value,
  onChange,
  onBlur,
  invalid = false,
  error,
  submitted = false,
  disabled = false,
  disabledReason,
  required = false,
  unit,
  type = 'text',
  options,
  className = '',
  ...rest
}) {
  const generatedId = useId();
  const inputId = hasText(id) ? id : `field-${generatedId}`;
  const declaredAdvanced = useDeclaredAdvancedFields();

  // The display gate, not the validation. See the module note.
  const [blurred, setBlurred] = useState(false);
  const [editing, setEditing] = useState(false);

  // ── Requirement 15.1 ──────────────────────────────────────────────────────
  // Asserted before anything is derived from it, so the failure names the missing
  // prop rather than surfacing later as an unlabelled control.
  assertContract(
    hasText(label),
    `Field${hasText(id) ? ` (${id})` : ''}: \`label\` is required and must be non-empty text `
      + '(Requirement 15.1). A placeholder is NOT a label — it is erased as soon as the trader '
      + 'types, and `Inp`\'s `aria-label={lbl || ph}` fallback is the behaviour this component exists '
      + 'to replace. There is no hidden-label option; if the label should not be read, the control '
      + 'should not be there.',
  );

  // ── Requirement 15.3 ──────────────────────────────────────────────────────
  assertContract(
    !disabled || hasText(disabledReason),
    `Field (${hasText(label) ? label : inputId}): \`disabled\` requires \`disabledReason\` `
      + '(Requirement 15.3). A greyed control with no stated reason leaves the trader guessing '
      + 'whether they lack a permission, need to stop something first, or have hit a bug.',
  );

  // ── Requirement 15.2 ──────────────────────────────────────────────────────
  assertContract(
    invalid !== true || hasText(error),
    `Field (${hasText(label) ? label : inputId}): \`invalid\` requires \`error\` (Requirement 15.2). `
      + 'The requirement asks for a message naming the field and the reason it is invalid, so a red '
      + 'border on its own satisfies neither half.',
  );

  // ── Requirement 15.6, the half that is visible from in here ───────────────
  // Rendered inside `ds/Accordion`, this field is part of the advanced set by
  // position, so it had better be in the set the form declared as data. The other
  // direction — declared advanced but rendered outside — is not knowable from a
  // single field and is what P31's DOM sweep is for.
  assertContract(
    declaredAdvanced === null || declaredAdvanced.includes(inputId),
    `Field (${hasText(label) ? label : inputId}): rendered inside an advanced-settings accordion `
      + `but \`${inputId}\` is not in that accordion's declared \`fields\` set `
      + `[${(declaredAdvanced || []).join(', ')}] (Requirement 15.6). The declared set is what makes `
      + 'the collapsed set checkable; a field that collapses without being declared is invisible to '
      + 'that check.',
  );

  const isSelect = Array.isArray(options) && options.length > 0;
  assertContract(
    !isSelect || options.every((option) => option && typeof option === 'object' && hasText(option.label)),
    `Field (${hasText(label) ? label : inputId}): every entry in \`options\` needs a non-empty `
      + '`label`. An option with no text is unreadable and unclickable.',
  );

  // The error treatment and the error message are the same event. `invalid` needs no
  // separate branch: the assertion above guarantees it comes with an `error`, so
  // gating on the message gates the border too, and a red control whose reason is
  // still hidden is not a state this component can reach.
  const showError = shouldShowError({
    hasError: hasText(error) || invalid === true,
    submitted: submitted === true,
    blurred,
    editing,
  });

  // ── The accessible description, in reading order ──────────────────────────
  // Standing help first, then the unit, then why the control is unusable, then what
  // is wrong with the value. Only ids that are actually rendered are listed:
  // `aria-describedby` pointing at a missing element is silently dropped by some
  // screen readers and read as empty by others.
  const hintId = `${inputId}-hint`;
  const unitId = `${inputId}-unit`;
  const reasonId = `${inputId}-reason`;
  const errorId = `${inputId}-error`;
  const showReason = disabled && hasText(disabledReason);
  const describedBy = [
    hasText(hint) ? hintId : null,
    hasText(unit) ? unitId : null,
    showReason ? reasonId : null,
    showError && hasText(error) ? errorId : null,
  ]
    .filter(Boolean)
    .join(' ');

  const handleChange = (event) => {
    // The trader is mid-repair; stop showing the previous verdict. Nothing is
    // parsed, rounded or reformatted here — the string goes straight out.
    setEditing(true);
    if (typeof onChange === 'function') onChange(event);
  };

  const handleBlur = (event) => {
    setBlurred(true);
    setEditing(false);
    if (typeof onBlur === 'function') onBlur(event);
  };

  const resolved = resolveInputType(type);
  const controlClass = [
    CONTROL_BASE,
    disabled ? CONTROL_DISABLED : showError ? CONTROL_INVALID : CONTROL_RESTING,
    hasText(unit) ? 'pr-12' : '',
    resolved.inputMode === 'decimal' ? 'font-mono' : '',
  ]
    .filter(Boolean)
    .join(' ');

  const shared = {
    id: inputId,
    value: value === undefined || value === null ? '' : value,
    onChange: handleChange,
    onBlur: handleBlur,
    disabled,
    // Redundant beside the native attribute and worth having: it survives a future
    // move to a focusable disabled control, and it is what a DOM-level check reads
    // when the element is a wrapper rather than the input itself.
    'aria-disabled': disabled ? 'true' : undefined,
    'aria-invalid': showError ? 'true' : undefined,
    'aria-describedby': describedBy === '' ? undefined : describedBy,
    required: required === true,
    'aria-required': required === true ? 'true' : undefined,
    className: controlClass,
    ...rest,
  };

  return (
    <div
      {...{ [FIELD_ATTR]: inputId }}
      data-field-invalid={showError ? 'true' : 'false'}
      data-field-disabled={disabled ? 'true' : 'false'}
      className={`flex min-w-0 flex-col gap-1 ${className}`.trim()}
    >
      <div className="flex items-baseline justify-between gap-2">
        {/* Visible, associated by `htmlFor`, and the only accessible name this
            control has. Requirement 15.1. */}
        <label htmlFor={inputId} className="text-small font-medium text-content-secondary">
          {label}
        </label>
        {/* The word, not an asterisk. An asterisk needs a legend somewhere else on
            the page to mean anything, and `*` alone is read as "star" or skipped. */}
        {required === true ? (
          <span className="text-micro uppercase tracking-wide text-content-muted">Required</span>
        ) : null}
      </div>

      <div className="relative flex items-center">
        {isSelect ? (
          <select {...shared}>
            {hasText(placeholder) ? <option value="">{placeholder}</option> : null}
            {options.map((option) => (
              <option key={String(option.value)} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        ) : (
          <input
            {...shared}
            type={resolved.type}
            inputMode={resolved.inputMode}
            placeholder={hasText(placeholder) ? placeholder : undefined}
            // A trading amount, symbol or key is not a word, and a browser offering
            // to autocomplete one from an unrelated form is a wrong-value hazard.
            autoComplete={resolved.inputMode === 'decimal' ? 'off' : rest.autoComplete}
            spellCheck={resolved.inputMode === 'decimal' ? false : rest.spellCheck}
          />
        )}
        {hasText(unit) ? (
          <span
            id={unitId}
            className="pointer-events-none absolute right-3 text-small text-content-secondary"
          >
            {unit}
          </span>
        ) : null}
      </div>

      {hasText(hint) ? (
        <p id={hintId} className="text-micro text-content-secondary">
          {hint}
        </p>
      ) : null}

      {/* Requirement 15.3, the visible half. Rendered whenever the control is
          disabled, so the reason and the greyed control always arrive together. */}
      {showReason ? (
        <p id={reasonId} className="text-micro text-content-secondary">
          {disabledReason}
        </p>
      ) : null}

      {/* Requirement 15.2. Inline, below the control it belongs to, and linked from
          that control alone — no page-level error summary duplicating it out of
          context. Not a live region; see the module note. */}
      {showError && hasText(error) ? (
        <p id={errorId} className="text-micro text-status-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export default Field;
