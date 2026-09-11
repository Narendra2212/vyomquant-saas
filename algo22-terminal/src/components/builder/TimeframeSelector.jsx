/**
 * TimeframeSelector.jsx — the DATA node's `timeframe` control. Task 7.3, Requirement 11.8.
 *
 * The option set is `GET /api/strategy-operations/registry/timeframes` and nothing else. That
 * matters more here than it looks:
 *
 * * The DATA descriptor publishes `timeframe` with **no** `options` tuple — `required=True,
 *   default=None` by SB-06 — so `ParameterForm` has nothing to render a `<select>` from. The
 *   endpoint is not one source among several; it is the only one.
 * * What it publishes is an **intersection** of the pipeline's own vocabularies (task 7.2):
 *   the backtester's frequency map, the live executor's interval table, and the market-data
 *   coverage gate's minutes table. `3m` is deliberately absent, because the coverage gate has
 *   no figure for it and a 3m strategy would run with its completeness check silently
 *   unfailable. A hand-written client list — "1m, 5m, 15m, 1h, 4h, 1d", which is what
 *   `DataPipelineContext.jsx` held until this task — would put unrunnable intervals straight
 *   back in front of the author and re-arm exactly that.
 *
 * So there is no local list here, and a failure renders an error with a retry rather than an
 * empty `<select>`: an empty interval list would read as a platform that trades on no
 * interval, which is untrue in a different direction.
 *
 * `seconds` comes off the wire too, shown beside each label. It is the figure a caller uses as
 * an expected bar interval, and parsing `"15m"` into a duration in the client would be a second
 * derivation of a number the endpoint already states — the drift that makes a month (`1M`) and
 * a minute (`1m`) indistinguishable.
 *
 * Accessibility: a native `<select>`, labelled by `ParameterForm`, keyboard-operable by
 * construction. The blank first option is never auto-resolved to the first real interval, and
 * the loading, error and ready states are announced as text through a live region.
 */

import React, { useId } from 'react';

import { useTimeframeSet } from '../../hooks/useTimeframeSet';

const SELECT_CLASS =
  'w-full rounded border bg-bg-elevated px-2 py-1 font-mono text-body text-text-primary ' +
  'focus:outline-none focus:ring-2 focus:ring-accent-cyan disabled:opacity-50';

const LINK_CLASS =
  'font-mono text-micro text-text-muted underline focus:outline-none focus:ring-2 ' +
  'focus:ring-accent-cyan';

const describedBy = (...ids) => {
  const joined = ids.filter(Boolean).join(' ');
  return joined === '' ? undefined : joined;
};

/** `900` → `15m`-worth of seconds, said in seconds. No label is parsed to get here. */
const secondsText = (seconds) => `${seconds}s`;

/**
 * @param {object}   props
 * @param {object}   props.controlProps The id / name / disabled / required / ARIA bundle from
 *   `ParameterForm`.
 * @param {object}   props.spec         The `timeframe` `ParamSpec`. Its `options` is empty by
 *   design, which is why this component exists.
 * @param {string|null} props.value
 * @param {Function} props.onChange     `(value) => void`; `null` clears the parameter.
 * @param {boolean}  props.disabled
 */
export function TimeframeSelector({
  controlProps = {},
  spec = {},
  value = null,
  onChange,
  disabled = false,
}) {
  const reactId = useId();
  const controlId = controlProps.id || `timeframe-${reactId}`;
  const statusId = `${controlId}-tf-status`;
  const errorId = `${controlId}-tf-error`;

  const { timeframes, sources, isLoading, isReady, isError, error, reload } = useTimeframeSet({
    enabled: !disabled,
  });

  const selected = typeof value === 'string' && value.trim() !== '' ? value.trim() : '';

  /**
   * A held value the endpoint no longer publishes. It is offered as an option so the control
   * can display what the graph actually holds — silently blanking it would make an author
   * think the field was empty when the saved graph still carries the interval — and it is
   * labelled as no longer supported, because it is a real problem the author has to fix.
   */
  const unsupportedSelection =
    isReady && selected !== '' && !timeframes.some((entry) => entry.id === selected);

  const statusText = () => {
    if (disabled) return 'Read-only: this version cannot be edited.';
    if (isLoading) return 'Loading the bar intervals the data pipeline supports…';
    if (isError) {
      return 'No intervals are listed: the supported interval set could not be loaded.';
    }
    if (isReady) {
      const count = timeframes.length;
      return (
        `${count} interval${count === 1 ? '' : 's'} every stage of the pipeline can process` +
        (sources.length > 0
          ? ` · intersection of ${sources.length} pipeline vocabular${sources.length === 1 ? 'y' : 'ies'}`
          : '')
      );
    }
    return 'The supported interval set has not been loaded yet.';
  };

  return (
    <div
      data-testid="timeframe-selector"
      data-state={isReady ? 'ready' : isLoading ? 'loading' : isError ? 'error' : 'idle'}
      data-timeframe-count={timeframes.length}
    >
      <select
        {...controlProps}
        className={
          SELECT_CLASS +
          (controlProps['aria-invalid'] ? ' border-accent-loss' : ' border-border-default')
        }
        aria-describedby={describedBy(
          controlProps['aria-describedby'],
          statusId,
          isError ? errorId : null,
        )}
        // Disabled while there is nothing real to choose from, so the control cannot be
        // operated into a value that came from nowhere.
        disabled={disabled || !isReady}
        value={selected}
        onChange={(event) => onChange && onChange(event.target.value === '' ? null : event.target.value)}
        data-testid="timeframe-select"
      >
        {/* Blank first and never auto-resolved: `timeframe` decides how a strategy reads the
            market, so the first published interval is not quietly chosen for the author
            (Requirement 5.4). */}
        <option value="">
          {spec.example ? `Select an interval… (e.g. ${spec.example})` : 'Select an interval…'}
        </option>
        {unsupportedSelection ? (
          <option value={selected} data-testid="timeframe-unsupported-option">
            {`${selected} — no longer supported`}
          </option>
        ) : null}
        {timeframes.map((entry) => (
          <option key={entry.id} value={entry.id} data-testid={`timeframe-option-${entry.id}`}>
            {`${entry.label} · ${secondsText(entry.seconds)}`}
          </option>
        ))}
      </select>

      <p
        id={statusId}
        role="status"
        aria-live="polite"
        className="mt-1 font-mono text-micro text-text-muted"
        data-testid="timeframe-selector-status"
      >
        {statusText()}
      </p>

      {unsupportedSelection ? (
        <p role="alert" className="mt-1 font-mono text-micro text-accent-loss" data-testid="timeframe-unsupported">
          {`This block holds “${selected}”, which the data pipeline no longer publishes. Choose a supported interval.`}
        </p>
      ) : null}

      {isError && error ? (
        <div
          id={errorId}
          role="alert"
          data-testid="timeframe-selector-error"
          data-code={error.code}
          data-status={error.status ?? undefined}
          className="mt-1 rounded border border-accent-loss bg-bg-elevated p-1.5"
        >
          {/* The client's own sentence here, because this failure is a transport outcome the
              registry endpoint did not author a body for; the code is carried so the cause is
              still machine-readable. */}
          <p className="font-mono text-micro text-accent-loss">{error.message}</p>
          {error.authExpired ? (
            <p className="font-mono text-micro text-text-muted">
              Sign in again — retrying will not help.
            </p>
          ) : (
            <button
              type="button"
              className={LINK_CLASS}
              onClick={reload}
              disabled={!error.retryable}
              data-testid="timeframe-retry"
            >
              Retry
            </button>
          )}
        </div>
      ) : null}
    </div>
  );
}

export default TimeframeSelector;
