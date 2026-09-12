/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ErrorBoundary — the last line of defence, and the last place a stack leaked
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.25. design.md §1.7, §12 ("Error Handling").
 * Requirements 14.3, 14.4.
 *
 * WHAT THIS FILE USED TO DO
 * ------------------------
 * It printed `error.message`, `error.stack` and `errorInfo.componentStack` into
 * the DOM inside a scrolling "ERROR DETAILS" box, and its "Copy Error" button
 * copied all three to the clipboard. Requirement 14.4 forbids exactly that as
 * user-facing content: a stack trace names modules, chunk paths and line numbers,
 * which maps the bundle for anyone reading over the trader's shoulder, and it
 * tells the trader nothing they can act on at the moment their screen went blank.
 * It also carried its own five-colour palette (documented here rather than in
 * code so the values are on record without being live literals: a near-black
 * canvas, a slate body colour, two reds and a grey), none of which came from
 * tokens.
 *
 * WHAT IT DOES NOW
 * ---------------
 *   * The words on screen are `translateError`'s output and nothing else. That
 *     rendering already exists once, in `ds/ErrorState`, so this file delegates
 *     to it rather than re-deriving four fields from the error — see REUSE below.
 *   * The stack and the component stack go to Sentry, and to the developer
 *     console. Neither reaches the DOM.
 *   * The Sentry event id is rendered as a report reference, so a support
 *     conversation has something to quote.
 *   * "Copy report details" copies `{eventId, timestamp, route}` — enough to find
 *     the event in Sentry, nothing that describes the bundle.
 *   * Every colour is a token utility. Its `no-colour-literals` budget entry is 0.
 *
 * REUSE: `ds/ErrorState` RENDERS THE COPY, THIS FILE RENDERS THE CHROME
 * -------------------------------------------------------------------
 * `ErrorState` is exactly the component for the message: it calls
 * `translateError(error, context)`, renders `headline`/`detail`/`action`/
 * `supportRef` and has no prop that can carry a message, so there is nowhere for
 * a stack to enter. Duplicating that here would mean two implementations of
 * Requirement 14.4's rendering, and the second one would not be the one with
 * tests. So it is reused as-is.
 *
 * It is NOT given `onRetry`. `ErrorState`'s retry re-issues a read; a boundary
 * has no read to re-issue, and its recovery is a reload. Offering both would put
 * two buttons on screen that do the same thing, so the reload lives in the chrome
 * below and `ErrorState` renders no retry button. Its own `action` — an expired
 * session's "Sign in", say — still renders, because that is the failure's next
 * step rather than the caller's.
 *
 * WHY THE BOUNDARY DOES NOT TRUST EITHER OF THEM
 * ---------------------------------------------
 * A component that throws while rendering an error boundary's fallback is not
 * caught by that boundary: React propagates it to the *next* boundary up, and
 * there is none above this one. The result is an unmounted tree — a blank page —
 * which is the one outcome this component exists to prevent. So the copy is
 * rendered inside `TranslationGuard`, a second, deliberately tiny boundary whose
 * fallback is static authored text and touches nothing but React.
 *
 * That is not theoretical. `translateError` runs the Requirement 14.4 scrubber
 * over its own output and **throws in development** when copy would leak, and
 * `ErrorState` calls it during render. `translateError` is total over its input
 * (property-tested at 300 iterations), so this should never fire — "should never"
 * is the reason to guard it rather than the reason not to.
 *
 * Everything the trader needs in order to recover — the reload button — sits
 * OUTSIDE that guard and is a plain `<button>`. The recovery affordance does not
 * depend on any component that can throw, which is why the two buttons here are
 * hand-written rather than `ds/ActionControl`; they carry `ActionControl`'s
 * classes so they still look like the design system's controls.
 */

import React from 'react';
import * as Sentry from '@sentry/react';

import { CONTEXT_COPY } from '../design/errorCopy';

import ErrorState from './ds/ErrorState';

/**
 * The static copy for the case where rendering the translated copy itself failed.
 *
 * Read off `CONTEXT_COPY` at module load — the same authored words `translateError`
 * would have returned for the `default` context — so this file invents no copy of
 * its own and there is still one place error wording lives. The `??` arms are for
 * the impossible case where the table is reshaped; a fallback that can throw is
 * not a fallback.
 */
const LAST_RESORT = Object.freeze({
  headline: CONTEXT_COPY?.default?.headline ?? 'Something went wrong',
  detail:
    CONTEXT_COPY?.default?.detail
    ?? 'This has been reported. Reload the page, and use the reference below if you contact support.',
});

/** `ds/ActionControl`'s shape, copied deliberately — see the docblock. */
const BUTTON_CLASS =
  'inline-flex items-center justify-center rounded-sm border px-3 py-1.5 text-body font-medium transition-colors';
const RELOAD_BUTTON_CLASS = `${BUTTON_CLASS} border-brand text-brand hover:bg-brand-wash`;
const COPY_BUTTON_CLASS = `${BUTTON_CLASS} border-line-strong text-content-secondary hover:bg-surface-raised hover:text-content-primary`;

/**
 * The path of the route that failed. Path only, never the query string or hash:
 * Supabase parks recovery and OAuth tokens there (`PasswordRecoveryHandler` in
 * `App.jsx` reads them), and a support ticket is a place credentials must not
 * reach. Wrapped because reading `location` is the kind of thing that is always
 * safe until it is not.
 */
function currentRoute() {
  try {
    return typeof window !== 'undefined' && window.location ? window.location.pathname : null;
  } catch {
    return null;
  }
}

/**
 * A boundary around the *fallback's* copy, so a throw there costs the words and
 * not the page.
 *
 * Its own render reads two frozen strings and calls nothing, which is the whole
 * point: it is the floor. `role="alert"` is repeated here because the announcement
 * lives on `ErrorState`'s element, and losing it would make the degraded screen
 * silent for a screen reader.
 */
class TranslationGuard extends React.Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error) {
    // Developer console only. This is the path that means the error-copy layer is
    // broken, so it needs to be loud somewhere — just not on the trader's screen.
    console.error('ErrorBoundary: rendering the translated copy failed', error);
  }

  render() {
    if (this.state.failed) {
      return (
        <div role="alert" className="flex flex-col items-center gap-2 text-center">
          <p className="text-title font-semibold text-content-primary">{LAST_RESORT.headline}</p>
          <p className="max-w-md text-body text-content-secondary">{LAST_RESORT.detail}</p>
        </div>
      );
    }
    return this.props.children;
  }
}

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      occurredAt: null,
      eventId: null,
      copied: false,
    };
  }

  /**
   * `occurredAt` is stamped here, when the failure happens, rather than read from
   * the clock during render — the previous version rendered `new Date()` inline,
   * so the timestamp it showed and copied was "now", not the moment of failure.
   */
  static getDerivedStateFromError(error) {
    return { hasError: true, error, occurredAt: new Date().toISOString() };
  }

  componentDidCatch(error, errorInfo) {
    // The stack and the component stack go to these two sinks and no others.
    console.error('ErrorBoundary caught an error:', error, errorInfo);

    let eventId = null;
    try {
      const reported = Sentry.captureException(error, {
        extra: {
          componentStack: errorInfo?.componentStack ?? null,
          route: currentRoute(),
        },
        tags: { boundary: 'app-root' },
      });
      // A scope with no client still mints an id for a report it never sends
      // (`main.jsx` skips `Sentry.init` for a placeholder DSN, which is the normal
      // state locally). Publishing that id would hand support a reference that
      // resolves to nothing, so it is only kept when a client exists to deliver it.
      const client = typeof Sentry.getClient === 'function' ? Sentry.getClient() : null;
      eventId = client && typeof reported === 'string' && reported !== '' ? reported : null;
    } catch (reportingFailure) {
      // Reporting is best-effort. It must never be the reason the fallback dies.
      console.error('ErrorBoundary could not report the error:', reportingFailure);
    }

    if (eventId !== null) this.setState({ eventId });
  }

  /**
   * What "Copy report details" puts on the clipboard: the three facts a support
   * ticket needs, and nothing that describes the code. No message, no stack, no
   * component stack, no query string.
   */
  reportDetails() {
    return JSON.stringify(
      {
        eventId: this.state.eventId,
        timestamp: this.state.occurredAt,
        route: currentRoute(),
      },
      null,
      2,
    );
  }

  handleCopy = () => {
    try {
      const clipboard = typeof navigator !== 'undefined' ? navigator.clipboard : null;
      // Absent on an insecure origin and in some embedded webviews. A button that
      // silently does nothing is better than one that throws out of an event handler.
      if (!clipboard || typeof clipboard.writeText !== 'function') return;

      const written = clipboard.writeText(this.reportDetails());
      if (written && typeof written.then === 'function') {
        written.then(
          () => this.setState({ copied: true }),
          () => {},
        );
      } else {
        this.setState({ copied: true });
      }
    } catch {
      /* A clipboard the browser refuses is not worth a second failure. */
    }
  };

  handleReload = () => {
    try {
      window.location.reload();
    } catch {
      /* Nothing left to try; the button simply does not fire. */
    }
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    const { error, eventId, occurredAt, copied } = this.state;

    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-surface-canvas px-6 py-12 font-sans">
        <div className="w-full max-w-md rounded-lg border border-line-default bg-surface-panel px-6 py-8">
          <TranslationGuard>
            <ErrorState error={error} context="default" />
          </TranslationGuard>

          {/* Two correlation facts, both non-diagnostic: an opaque event id and a
              time. Selectable and in mono, because their only job is to be read
              out or pasted into a ticket. */}
          {eventId !== null ? (
            <p className="mt-4 text-center text-micro text-content-muted">
              Report reference <span className="font-mono text-content-secondary">{eventId}</span>
            </p>
          ) : null}
          {occurredAt !== null ? (
            <p className="mt-1 text-center text-micro text-content-muted">
              <span className="font-mono">{occurredAt}</span>
            </p>
          ) : null}

          <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
            <button type="button" onClick={this.handleReload} className={RELOAD_BUTTON_CLASS}>
              Reload page
            </button>
            <button type="button" onClick={this.handleCopy} className={COPY_BUTTON_CLASS}>
              Copy report details
            </button>
          </div>

          {copied ? (
            <p role="status" className="mt-2 text-center text-micro text-content-secondary">
              Copied. It holds the reference, the time and the page — no diagnostics.
            </p>
          ) : null}
        </div>
      </div>
    );
  }
}
