/**
 * ParameterForm.jsx — the block inspector's parameter form, generated entirely from the
 * registry's `ParamSpec` wire shape (Requirement 5.1).
 *
 * The descriptor is authoritative. `ParamSpec` lives in
 * `backend_app/backend/strategy_dag/block_specs.py` and is re-exported by `registry.py`;
 * its `to_dict()` is the shape this form consumes:
 *
 *   { key, label, type, required, default, min, max, step, options, options_source,
 *     unit, example, help, depends_on, affects_warmup }
 *
 * Descriptors arrive as **props**, never imported from a registry client, so this component
 * stays pure, testable, and free of a second source of truth.
 *
 * Financial-safety rule this file exists to hold (Requirement 5.4)
 * ---------------------------------------------------------------
 * A parameter that changes trading behaviour is published `required=True, default=None` —
 * `ParamSpec.__post_init__` rejects any attempt to publish one with a default. This form
 * therefore renders those parameters **empty and blocking** until the author sets them:
 *
 *   * no prefill, ever — `BEHAVIOUR_CHANGING_PARAMS` values are read from `values` alone and
 *     `spec.default` is ignored for those keys even if a malformed descriptor carried one;
 *   * `example` is an input `placeholder` and a SELECT's blank-option hint, never a value;
 *   * a SELECT starts on a blank option — the first option is never "helpfully" picked;
 *   * a BOOLEAN with no value and no declared default renders as a blank tri-state select
 *     rather than an unchecked checkbox, because an unchecked box silently means `false`.
 *
 * A silently defaulted `quantity` is a position size nobody chose; a silently defaulted
 * `symbol` is a trade on the wrong market. Neither is a UX preference.
 *
 * One message source (Requirement 8.9)
 * ------------------------------------
 * Inline validation text is the backend's own: `fix_hint` first, `message` second, both
 * verbatim from the structured issue contract
 * `{code, severity, node_id, edge_id, field, message, expected, actual, fix_hint}`
 * (`schema.make_issue`). Issues are matched to fields by `field`. This form authors **no**
 * parallel validation copy: when a field carries a backend issue the backend text is the
 * only text shown for it. The one client-side string is the neutral "Required · not set"
 * status on an untouched required field, which exists because the backend has not seen that
 * field yet — it is replaced, not supplemented, the moment a backend issue arrives.
 *
 * Severity is normalised the way the backend validator normalises it: `registry.py` says
 * "ERROR", `schema.py` says "error". Anything unrecognised fails closed to `error`.
 */

import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';

/** The full `ParamType` vocabulary. Every member below is rendered by `controlFor`. */
export const PARAM_TYPES = Object.freeze([
  'NUMBER',
  'INTEGER',
  'TEXT',
  'SELECT',
  'MULTISELECT',
  'BOOLEAN',
  'DATE',
  'SYMBOL',
  'TIMEFRAME',
]);

/**
 * Parameters that materially change trading behaviour. Mirrors `NO_DEFAULT_PARAMS` in
 * `block_specs.py` — deliberately the backend's full list, which is a superset of the seven
 * named in the requirement (it also covers `limit_price` and `direction`). Widening the
 * safety net is safe; narrowing it is not.
 */
export const BEHAVIOUR_CHANGING_PARAMS = Object.freeze([
  'symbol',
  'timeframe',
  'quantity',
  'quantity_type',
  'price',
  'trigger_price',
  'limit_price',
  'confidence_threshold',
  'direction',
]);

const SEVERITY_ERROR = 'error';
const SEVERITY_WARNING = 'warning';

/** Fail closed, exactly as the backend validator's `normalise_severity` does. */
export const normaliseSeverity = (value) => {
  const text = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return text.startsWith('warn') ? SEVERITY_WARNING : SEVERITY_ERROR;
};

/** True when a value is absent rather than chosen. An empty multi-selection is absent. */
export const isUnset = (value) =>
  value === null ||
  value === undefined ||
  (typeof value === 'string' && value.trim() === '') ||
  (Array.isArray(value) && value.length === 0) ||
  (typeof value === 'number' && Number.isNaN(value));

/** True when `key` must never be prefilled (Requirement 5.4). */
export const isBehaviourChanging = (key) => BEHAVIOUR_CHANGING_PARAMS.includes(key);

const sameValue = (a, b) => {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((item, index) => sameValue(item, b[index]));
  }
  return a === b;
};

const hasDeclaredDefault = (spec) => spec.default !== null && spec.default !== undefined;

/**
 * The resolved state of one parameter: what to show, whether it is the block's default and
 * whether it blocks the save. Pure, and exported so blocking can be asserted without a DOM.
 */
export const describeField = (spec, values = {}, issues = []) => {
  const behaviourChanging = isBehaviourChanging(spec.key);
  const raw = values ? values[spec.key] : undefined;
  const declaredDefault = hasDeclaredDefault(spec);

  let value = raw;
  let showingDefault = false;

  if (behaviourChanging) {
    // The descriptor withheld a default on purpose. Never substitute one.
    value = raw;
  } else if (isUnset(raw) && declaredDefault) {
    value = spec.default;
    showingDefault = true;
  } else if (declaredDefault && sameValue(raw, spec.default)) {
    showingDefault = true;
  }

  const fieldIssues = (issues || []).filter((issue) => issue && issue.field === spec.key);
  const errors = fieldIssues.filter((issue) => normaliseSeverity(issue.severity) === SEVERITY_ERROR);
  const unset = isUnset(value);

  return {
    key: spec.key,
    spec,
    behaviourChanging,
    value,
    unset,
    showingDefault,
    canResetToDefault: declaredDefault && !showingDefault,
    blocking: Boolean(spec.required) && unset,
    issues: fieldIssues,
    hasError: errors.length > 0,
    severity: fieldIssues.length
      ? errors.length
        ? SEVERITY_ERROR
        : SEVERITY_WARNING
      : null,
  };
};

/** Keys of every required parameter still unset — the save blockers (Requirement 5.4). */
export const blockingParamKeys = (params = [], values = {}) =>
  (params || [])
    .filter((spec) => spec && describeField(spec, values).blocking)
    .map((spec) => spec.key);

/** Issues whose `field` names no published parameter, so nothing is silently swallowed. */
const unmatchedIssues = (params = [], issues = []) => {
  const keys = new Set((params || []).map((spec) => spec && spec.key));
  return (issues || []).filter((issue) => issue && issue.field && !keys.has(issue.field));
};

const formatScalar = (value) => {
  if (value === null || value === undefined) return '';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
};

const formatForInput = (value) => (isUnset(value) ? '' : formatScalar(value));

/**
 * Bring a DOM string back to the type the descriptor declared, by matching it against the
 * declared option set. `feat_lag.lags` options are numbers; `<select>` hands back strings.
 */
const coerceToOption = (raw, options) => {
  if (!Array.isArray(options)) return raw;
  const match = options.find((option) => formatScalar(option) === raw);
  return match === undefined ? raw : match;
};

const parseNumeric = (raw, integer) => {
  if (typeof raw !== 'string' || raw.trim() === '') return null;
  const parsed = integer ? Number.parseInt(raw, 10) : Number(raw);
  // A value we cannot parse is handed on unchanged so the backend validator reports it in
  // its own words, rather than this form inventing a type message.
  return Number.isFinite(parsed) ? parsed : raw;
};

const constraintText = (spec) => {
  const parts = [];
  if (spec.unit) parts.push(spec.unit);
  if (spec.min !== null && spec.min !== undefined && spec.max !== null && spec.max !== undefined) {
    parts.push(`${spec.min}–${spec.max}`);
  } else if (spec.min !== null && spec.min !== undefined) {
    parts.push(`min ${spec.min}`);
  } else if (spec.max !== null && spec.max !== undefined) {
    parts.push(`max ${spec.max}`);
  }
  if (spec.step !== null && spec.step !== undefined) parts.push(`step ${spec.step}`);
  if (Array.isArray(spec.options) && spec.options.length) {
    parts.push(`${spec.options.length} options`);
  }
  if (!isUnset(spec.example)) parts.push(`e.g. ${formatScalar(spec.example)}`);
  if (spec.affects_warmup) parts.push('affects warmup');
  if (Array.isArray(spec.depends_on) && spec.depends_on.length) {
    parts.push(`depends on ${spec.depends_on.join(', ')}`);
  }
  return parts.join(' · ');
};

const CONTROL_CLASS =
  'w-full rounded border bg-bg-elevated px-2 py-1 font-mono text-body text-text-primary ' +
  'placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-accent-cyan ' +
  'disabled:opacity-50';

const controlClassName = (field) =>
  `${CONTROL_CLASS} ${
    field.hasError || field.blocking ? 'border-accent-loss' : 'border-border-default'
  }`;

/**
 * One parameter. Every control is labelled, described and marked invalid through ARIA, so
 * the blocking state is never signalled by colour alone.
 */
function ParameterField({
  field,
  idPrefix,
  disabled,
  resolveOptions,
  onChange,
  controls,
  params,
  values,
}) {
  const { spec } = field;
  const controlId = `${idPrefix}-${spec.key}`;
  const helpId = `${controlId}-help`;
  const constraintsId = `${controlId}-constraints`;
  const statusId = `${controlId}-status`;
  const messageId = `${controlId}-message`;
  const [helpOpen, setHelpOpen] = useState(false);
  const [draft, setDraft] = useState(null);

  const help = typeof spec.help === 'string' ? spec.help.trim() : '';
  // 19 of the 48 `ml_models` hyperparameters carry `help=""` today. A missing help string
  // renders no tooltip trigger and no help region at all, rather than an empty tooltip.
  const hasHelp = help !== '';
  const constraints = constraintText(spec);

  const describedBy = [
    hasHelp ? helpId : null,
    constraints ? constraintsId : null,
    field.issues.length ? messageId : field.blocking ? statusId : null,
  ]
    .filter(Boolean)
    .join(' ');

  const emit = useCallback(
    (value) => {
      if (onChange) onChange(spec.key, value, spec);
    },
    [onChange, spec],
  );

  const declaredOptions = Array.isArray(spec.options) && spec.options.length ? spec.options : null;
  const resolvedOptions = useMemo(() => {
    if (declaredOptions) return declaredOptions;
    if (spec.options_source && resolveOptions) {
      const supplied = resolveOptions(spec);
      if (Array.isArray(supplied) && supplied.length) return supplied;
    }
    return null;
  }, [declaredOptions, resolveOptions, spec]);

  const commonProps = {
    id: controlId,
    name: spec.key,
    disabled,
    required: Boolean(spec.required),
    'aria-required': Boolean(spec.required),
    'aria-invalid': field.hasError || field.blocking,
    'aria-describedby': describedBy || undefined,
    className: controlClassName(field),
    'data-param-key': spec.key,
    'data-param-type': spec.type,
  };

  const numericProps = () => ({
    type: 'number',
    inputMode: spec.type === 'INTEGER' ? 'numeric' : 'decimal',
    value: draft !== null ? draft : formatForInput(field.value),
    placeholder: formatForInput(spec.example),
    min: spec.min ?? undefined,
    max: spec.max ?? undefined,
    // An INTEGER with no declared step still steps by 1; a NUMBER without one accepts
    // decimals rather than being silently rounded to whole numbers by the browser.
    step: spec.step ?? (spec.type === 'INTEGER' ? 1 : 'any'),
    onChange: (event) => {
      setDraft(event.target.value);
      emit(parseNumeric(event.target.value, spec.type === 'INTEGER'));
    },
    onBlur: () => setDraft(null),
  });

  const textProps = (type) => ({
    type,
    value: formatForInput(field.value),
    placeholder: formatForInput(spec.example),
    onChange: (event) => emit(event.target.value === '' ? null : event.target.value),
  });

  const blankOptionLabel = isUnset(spec.example)
    ? 'Select…'
    : `Select… (e.g. ${formatScalar(spec.example)})`;

  /**
   * A caller-supplied control for this `ParamType`, if there is one (task 7.3).
   *
   * The seam exists because two of the nine types have option sets that live outside the
   * descriptor and cannot be rendered from it: SYMBOL's comes from the asset discovery
   * endpoint (Requirement 11.7) and TIMEFRAME's from the registry's timeframe set
   * (Requirement 11.8). Both need search, filtering, pagination and their own error states,
   * which is more than a `<datalist>` can carry.
   *
   * It is an *injection point*, not a client: this file still imports nothing that fetches,
   * so `ParameterForm` stays pure and every guarantee below it — no prefill of a
   * behaviour-changing parameter, no auto-selected first option, backend text verbatim —
   * continues to be assertable without a network. With no `controls` prop the form behaves
   * exactly as it did before.
   */
  const Control = controls && spec.type ? controls[spec.type] : undefined;

  const renderControl = () => {
    if (typeof Control === 'function') {
      return (
        <Control
          // `commonProps` carries the id, name, disabled, required and every ARIA attribute
          // the label and description wiring depends on, so a supplied control inherits the
          // form's accessibility contract rather than re-deriving it.
          controlProps={commonProps}
          spec={spec}
          field={field}
          value={field.value}
          disabled={disabled}
          onChange={emit}
          // Sibling specs and values: the asset selector reads the DATA block's own
          // `market_type` options from its `ParamSpec` rather than holding a copy.
          params={params}
          values={values}
        />
      );
    }

    switch (spec.type) {
      case 'NUMBER':
      case 'INTEGER':
        return <input {...commonProps} {...numericProps()} />;

      case 'TEXT':
        return <input {...commonProps} {...textProps('text')} />;

      case 'DATE':
        return <input {...commonProps} {...textProps('date')} />;

      case 'SYMBOL':
      case 'TIMEFRAME': {
        // The option sets for these live outside the descriptor — the asset universe
        // (Requirement 11.7) and the registry timeframe set (Requirement 11.8). Until a
        // caller supplies them the control is a free-text field with the example as its
        // placeholder. It is never prefilled from either source.
        const listId = resolvedOptions ? `${controlId}-list` : undefined;
        return (
          <>
            <input {...commonProps} {...textProps('text')} list={listId} autoComplete="off" />
            {resolvedOptions ? (
              <datalist id={listId}>
                {resolvedOptions.map((option) => (
                  <option key={formatScalar(option)} value={formatScalar(option)} />
                ))}
              </datalist>
            ) : null}
          </>
        );
      }

      case 'SELECT':
        return (
          <select
            {...commonProps}
            value={formatForInput(field.value)}
            onChange={(event) =>
              emit(
                event.target.value === ''
                  ? null
                  : coerceToOption(event.target.value, resolvedOptions),
              )
            }
          >
            {/* Blank first: the first real option is never auto-selected. */}
            <option value="">{blankOptionLabel}</option>
            {(resolvedOptions || []).map((option) => (
              <option key={formatScalar(option)} value={formatScalar(option)}>
                {formatScalar(option)}
              </option>
            ))}
          </select>
        );

      case 'MULTISELECT': {
        const selected = Array.isArray(field.value) ? field.value.map(formatScalar) : [];
        return (
          <select
            {...commonProps}
            multiple
            value={selected}
            onChange={(event) =>
              emit(
                Array.from(event.target.selectedOptions).map((option) =>
                  coerceToOption(option.value, resolvedOptions),
                ),
              )
            }
          >
            {(resolvedOptions || []).map((option) => (
              <option key={formatScalar(option)} value={formatScalar(option)}>
                {formatScalar(option)}
              </option>
            ))}
          </select>
        );
      }

      case 'BOOLEAN':
        // No value and no declared default: an unchecked checkbox would silently mean
        // `false`, so the choice stays genuinely unmade.
        if (field.unset) {
          return (
            <select
              {...commonProps}
              value=""
              onChange={(event) => emit(event.target.value === '' ? null : event.target.value === 'true')}
            >
              <option value="">{blankOptionLabel}</option>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          );
        }
        return (
          <input
            {...commonProps}
            // `required` on a checkbox means "must be ticked", which is not what a required
            // boolean parameter means. `aria-required` still announces it.
            required={undefined}
            type="checkbox"
            className="h-4 w-4 rounded border-border-default bg-bg-elevated accent-accent-cyan focus:outline-none focus:ring-2 focus:ring-accent-cyan disabled:opacity-50"
            checked={field.value === true}
            onChange={(event) => emit(event.target.checked)}
          />
        );

      default:
        // An unhandled `ParamType` is surfaced, never silently skipped: the author still
        // gets a usable control, and the gap is stated in the UI and in the DOM.
        return (
          <>
            <input {...commonProps} {...textProps('text')} />
            <p
              role="status"
              className="mt-1 font-mono text-micro text-accent-gold"
              data-testid={`unsupported-${spec.key}`}
              data-unsupported-param-type={String(spec.type)}
            >
              Unsupported control type “{String(spec.type)}” — shown as free text so it is not
              dropped. Report this: the registry published a type this form does not know.
            </p>
          </>
        );
    }
  };

  const optionSetPending =
    (spec.type === 'SELECT' || spec.type === 'MULTISELECT') &&
    !resolvedOptions &&
    Boolean(spec.options_source);

  return (
    <div
      className="border-b border-border-subtle py-2 last:border-b-0"
      data-testid={`param-${spec.key}`}
      data-blocking={field.blocking ? 'true' : 'false'}
    >
      <div className="mb-1 flex items-baseline gap-1.5">
        <label
          htmlFor={controlId}
          className="font-mono text-micro uppercase tracking-wider text-text-secondary"
        >
          {spec.label}
        </label>
        {spec.required ? (
          <span className="font-mono text-micro text-accent-cyan" data-testid={`required-${spec.key}`}>
            required
          </span>
        ) : null}
        {field.showingDefault ? (
          <span
            className="rounded border border-border-default bg-bg-elevated px-1 font-mono text-micro text-text-muted"
            data-testid={`default-badge-${spec.key}`}
            title={`Block default: ${formatScalar(spec.default)}`}
          >
            default
          </span>
        ) : null}
        {hasHelp ? (
          <button
            type="button"
            className="ml-auto rounded border border-border-default px-1 font-mono text-micro text-text-muted focus:outline-none focus:ring-2 focus:ring-accent-cyan"
            aria-expanded={helpOpen}
            aria-controls={helpId}
            aria-label={`Help for ${spec.label}`}
            onClick={() => setHelpOpen((open) => !open)}
          >
            ?
          </button>
        ) : null}
      </div>

      {renderControl()}

      {/* Always in the DOM so `aria-describedby` resolves; the button reveals it visually. */}
      {hasHelp ? (
        <p
          id={helpId}
          role="tooltip"
          data-testid={`help-${spec.key}`}
          data-open={helpOpen ? 'true' : 'false'}
          className={
            helpOpen
              ? 'mt-1 rounded border border-border-default bg-bg-elevated p-1.5 text-micro text-text-secondary'
              : 'sr-only'
          }
        >
          {help}
        </p>
      ) : null}

      {constraints ? (
        <p id={constraintsId} className="mt-1 font-mono text-micro text-text-muted">
          {constraints}
        </p>
      ) : null}

      {optionSetPending ? (
        <p className="mt-1 font-mono text-micro text-text-muted">
          Options resolved from the graph ({spec.options_source}).
        </p>
      ) : null}

      {field.issues.length ? (
        <div id={messageId} data-testid={`issue-${spec.key}`}>
          {field.issues.map((issue, index) => (
            <p
              key={`${issue.code || 'issue'}-${index}`}
              role={normaliseSeverity(issue.severity) === SEVERITY_ERROR ? 'alert' : 'status'}
              className={`mt-1 text-micro ${
                normaliseSeverity(issue.severity) === SEVERITY_ERROR
                  ? 'text-accent-loss'
                  : 'text-accent-gold'
              }`}
              data-severity={normaliseSeverity(issue.severity)}
              data-code={issue.code || undefined}
            >
              {/* Backend text only, verbatim (Requirement 8.9). */}
              <span className="font-mono uppercase">
                {normaliseSeverity(issue.severity)}
              </span>{' '}
              {issue.fix_hint ? issue.fix_hint : issue.message}
              {issue.fix_hint && issue.message && issue.message !== issue.fix_hint ? (
                <span className="block text-text-secondary">{issue.message}</span>
              ) : null}
            </p>
          ))}
        </div>
      ) : field.blocking ? (
        <p id={statusId} className="mt-1 font-mono text-micro text-accent-loss">
          Required · not set
          {field.behaviourChanging
            ? ' — this parameter changes how the strategy trades, so it has no default.'
            : ''}
        </p>
      ) : null}

      {field.canResetToDefault && !disabled ? (
        <button
          type="button"
          className="mt-1 font-mono text-micro text-text-muted underline focus:outline-none focus:ring-2 focus:ring-accent-cyan"
          data-testid={`reset-default-${spec.key}`}
          onClick={() => onChange && onChange(spec.key, spec.default, spec)}
        >
          Reset to default ({formatScalar(spec.default)})
        </button>
      ) : null}
    </div>
  );
}

/**
 * Parameter form for one selected node.
 *
 * @param {object}   props
 * @param {Array}    props.params            `ParamSpec.to_dict()` entries from the registry.
 * @param {object}   props.values            Current parameter values for the node.
 * @param {Function} props.onChange          `(key, value, spec) => void`.
 * @param {Array}    props.issues            Structured backend issues (`schema.make_issue`).
 * @param {string}   props.nodeId            Node the form is editing; filters `issues`.
 * @param {string}   props.blockId           Block identifier, for the heading.
 * @param {Function} props.resolveOptions    `(spec) => options|null` for `options_source`,
 *                                           and for SYMBOL / TIMEFRAME candidate lists.
 * @param {object}   props.controls          Optional `ParamType` → component overrides, e.g.
 *                                           `{SYMBOL: AssetSelector, TIMEFRAME: TimeframeSelector}`
 *                                           (task 7.3). Each receives
 *                                           `{controlProps, spec, field, value, disabled, onChange, params, values}`.
 *                                           Absent means every type renders from the
 *                                           descriptor exactly as before.
 * @param {Function} props.onBlockingChange  `(keys) => void`, the unset required keys.
 * @param {boolean}  props.disabled          Read-only, e.g. a DEPLOYED version (Req 9.9).
 */
export function ParameterForm({
  params = [],
  values = {},
  onChange,
  issues = [],
  nodeId,
  blockId,
  resolveOptions,
  controls,
  onBlockingChange,
  disabled = false,
}) {
  const reactId = useId();
  const idPrefix = `pf-${nodeId || blockId || reactId}`.replace(/[^\w-]/g, '-');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const advancedId = `${idPrefix}-advanced`;

  const specs = useMemo(
    () => (params || []).filter((spec) => spec && typeof spec.key === 'string'),
    [params],
  );

  // A node-scoped issue set: an issue naming another node is not this form's business.
  const scopedIssues = useMemo(
    () =>
      (issues || []).filter(
        (issue) => issue && (!nodeId || !issue.node_id || issue.node_id === nodeId),
      ),
    [issues, nodeId],
  );

  const fields = useMemo(
    () => specs.map((spec) => describeField(spec, values, scopedIssues)),
    [specs, values, scopedIssues],
  );

  const required = fields.filter((field) => field.spec.required);
  const optional = fields.filter((field) => !field.spec.required);
  const blocking = fields.filter((field) => field.blocking).map((field) => field.key);
  const orphanIssues = useMemo(() => unmatchedIssues(specs, scopedIssues), [specs, scopedIssues]);

  // An error against a parameter hidden under "Advanced" would otherwise be unreadable.
  const optionalHasError = optional.some((field) => field.hasError);
  useEffect(() => {
    if (optionalHasError) setAdvancedOpen(true);
  }, [optionalHasError]);

  // The callback is held in a ref so an inline arrow prop cannot turn this into a render
  // loop: the notification fires when the blocking set changes, and only then.
  const blockingKey = blocking.join('\u0000');
  const notifyRef = useRef(onBlockingChange);
  notifyRef.current = onBlockingChange;
  useEffect(() => {
    if (notifyRef.current) {
      notifyRef.current(blockingKey === '' ? [] : blockingKey.split('\u0000'));
    }
  }, [blockingKey]);

  const fieldProps = { idPrefix, disabled, resolveOptions, onChange, controls, params: specs, values };

  if (!specs.length) {
    return (
      <div className="p-2 font-mono text-micro text-text-muted" data-testid="parameter-form-empty">
        This block declares no parameters.
      </div>
    );
  }

  return (
    <div
      role="group"
      aria-label={`Parameters${blockId ? ` for ${blockId}` : ''}`}
      className="flex flex-col"
      data-testid="parameter-form"
      data-block-id={blockId || undefined}
      data-node-id={nodeId || undefined}
      data-blocking-count={blocking.length}
    >
      {/* Blocking state is text, not colour: the save gate is stated in words. */}
      {blocking.length ? (
        <p
          role="status"
          className="mb-1 rounded border border-accent-loss bg-bg-elevated p-1.5 font-mono text-micro text-accent-loss"
          data-testid="parameter-form-blocking"
          data-blocking-keys={blocking.join(',')}
        >
          {blocking.length === 1
            ? '1 required parameter is not set. Saving is blocked until it is.'
            : `${blocking.length} required parameters are not set. Saving is blocked until they are.`}
        </p>
      ) : null}

      {/* Requirement 5.3: every required parameter is visible, with nothing to expand. */}
      <div data-testid="required-params">
        {required.map((field) => (
          <ParameterField key={field.key} field={field} {...fieldProps} />
        ))}
      </div>

      {optional.length ? (
        <div className="mt-2">
          <button
            type="button"
            className="flex w-full items-center gap-1 border-t border-border-default pt-2 font-mono text-micro uppercase tracking-wider text-text-secondary focus:outline-none focus:ring-2 focus:ring-accent-cyan"
            aria-expanded={advancedOpen}
            aria-controls={advancedId}
            data-testid="advanced-toggle"
            onClick={() => setAdvancedOpen((open) => !open)}
          >
            <span aria-hidden="true">{advancedOpen ? '▾' : '▸'}</span>
            Advanced ({optional.length})
          </button>
          <div id={advancedId} data-testid="advanced-params" hidden={!advancedOpen}>
            {advancedOpen
              ? optional.map((field) => (
                  <ParameterField key={field.key} field={field} {...fieldProps} />
                ))
              : null}
          </div>
        </div>
      ) : null}

      {/* An issue naming a field this block does not publish is shown, not discarded. */}
      {orphanIssues.length ? (
        <div className="mt-2" data-testid="unmatched-issues">
          {orphanIssues.map((issue, index) => (
            <p
              key={`${issue.code || 'issue'}-${index}`}
              role="alert"
              className="font-mono text-micro text-accent-gold"
              data-field={issue.field}
            >
              {issue.field}: {issue.fix_hint ? issue.fix_hint : issue.message}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default ParameterForm;
