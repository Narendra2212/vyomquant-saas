/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/ErrorState — the only way a failure reaches the screen
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.1. design.md §5.1, §11.1, §12.
 * Requirements 14.3, 14.4, 19.4.
 *
 * THE ONE RULE: IT NEVER READS `error.message`
 * -------------------------------------------
 * There is no branch in this file that touches the error object at all. It calls
 * `translateError(error, context)` and renders four fields off the result —
 * `headline`, `detail`, `action`, `supportRef` — and that is the whole component.
 * The property being protected is Requirement 14.4's: no HTTP status, no exception
 * class name, no stack frame, no internal endpoint path as user-facing content.
 *
 * Making it structural rather than careful matters because the legacy path was
 * careful and still leaked. `extractErrorMessage` in `ui-legacy/primitives.jsx`
 * falls through to `JSON.stringify(detail)` and then `err.message`, so an axios
 * timeout string and a FastAPI validation dump both reach the screen verbatim; the
 * legacy `ErrorState` then takes `title` and `message` as *props*, which means every
 * call site is free to pass whatever it caught. This component has no prop that can
 * carry a message. There is nowhere to put one.
 *
 * RETRY IS OFFERED IFF THE TRANSLATION SAYS RETRYABLE
 * --------------------------------------------------
 * `onRetry` alone is not enough. Retryability is a property of the failure, not of
 * the caller's willingness to try again: `ApiError.isRetryable()` already knows that
 * a 503 is worth repeating and that `MARKETPLACE_NOT_SUBSCRIBED` is not, and
 * `translateError` reports it as `retryable`. A retry button on a permanent refusal
 * is worse than no button — it invites a trader to keep pressing something that will
 * keep failing, during the minutes when they most need to know it will not work.
 * So both must hold: the failure is retryable AND the caller supplied a way to retry.
 *
 * `supportRef` IS A CORRELATION REFERENCE, NOT A DIAGNOSTIC
 * -------------------------------------------------------
 * `apiClient` mints a request id per request and the backend's exception handler
 * returns its own `request_id`; either is a random identifier — not a status, not a
 * class, not a path — which is exactly what makes "contact support" actionable
 * without disclosing anything about the server. It is rendered in mono at
 * `--text-micro`, labelled, and selectable, because its only job is to be copied
 * into a support ticket.
 *
 * IT IS ANNOUNCED
 * --------------
 * `role="alert"`. This is the one panel state that both interrupts and discards: a
 * failed read drops the previous payload (`usePanelState`, Requirement 14.5), so
 * whatever a trader was reading has just been removed from the screen. An assertive
 * announcement is proportionate to that. `EmptyState` deliberately does not do this.
 */

import { AlertTriangle, RotateCcw } from 'lucide-react';

import { translateError } from '../../design/errorCopy';
import { statusToken } from '../../design/semantic';

import { ActionControl } from './ActionControl';
import { hasText } from './devAssert';

/**
 * A failure, in words a trader can act on.
 *
 * @param {Object} props
 * @param {*} [props.error] Whatever was caught — an `ApiError`, a `subscription_refused`
 *   frame, a bare `Error`, a string, `null`. `translateError` is total over all of them,
 *   so there is no input that renders a blank panel.
 * @param {string} [props.context] Selects the copy family in `design/errorCopy.js`
 *   (`'positions'`, `'dashboard'`, `'backtester'`, …). Used only for the last-resort copy.
 * @param {Function} [props.onRetry] Re-issues the read. Rendered ONLY when the
 *   translation reports the failure retryable.
 * @param {boolean} [props.compact] Tighter padding and a smaller icon, for an inline
 *   region rather than a whole panel body.
 * @param {string} [props.className]
 */
export function ErrorState({ error, context, onRetry, compact = false, className = '', ...rest }) {
  // The ONLY read of `error` in this component. Everything below renders `copy`.
  const copy = translateError(error, context);
  const { fg } = statusToken('error');

  const canRetry = copy.retryable === true && typeof onRetry === 'function';

  return (
    <div
      role="alert"
      data-error-retryable={copy.retryable === true ? 'true' : 'false'}
      className={`flex flex-col items-center gap-2 text-center ${compact ? 'px-3 py-4' : 'px-4 py-8'} ${className}`.trim()}
      {...rest}
    >
      {/* Colour from `semantic.js`'s `error` group, never a literal and never a prop
          (design.md §5 — no primitive accepts a colour). */}
      <AlertTriangle
        size={compact ? 20 : 26}
        strokeWidth={1.75}
        aria-hidden="true"
        style={{ color: fg }}
      />

      <p className="text-title font-semibold text-content-primary">{copy.headline}</p>
      {hasText(copy.detail) ? (
        <p className="max-w-md text-body text-content-secondary">{copy.detail}</p>
      ) : null}

      {canRetry || copy.action ? (
        <div className="mt-1 flex flex-wrap items-center justify-center gap-2">
          {canRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex items-center gap-1.5 rounded-sm border border-brand px-3 py-1.5 text-body font-medium text-brand transition-colors hover:bg-brand-wash"
            >
              <RotateCcw size={13} strokeWidth={2} aria-hidden="true" />
              Try again
            </button>
          ) : null}
          {/* The translation's own next step — `AUTH_ERROR`'s "Sign in", an expired
              subscription's "View listing". Secondary when a retry is also offered. */}
          <ActionControl action={copy.action} intent={canRetry ? 'secondary' : 'primary'} />
        </div>
      ) : null}

      {hasText(copy.supportRef) ? (
        <p className="mt-1 text-micro text-content-muted">
          Support reference{' '}
          <span className="font-mono text-content-secondary">{copy.supportRef}</span>
        </p>
      ) : null}
    </div>
  );
}

export default ErrorState;
