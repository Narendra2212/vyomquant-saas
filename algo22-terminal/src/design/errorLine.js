/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ERROR COPY, AS ONE LINE — for the surfaces that hold a string, not a panel
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * `design.md` → "Error Handling" (§12). Requirements 14.3 and 14.4.
 * vyomquant-ui-redesign task 10.8.
 *
 * WHY THIS EXISTS
 * ---------------
 * `translateError` returns four authored fields, and `ds/ErrorState` renders them as four
 * elements. Two surfaces in the tree cannot do that: the legacy `Toast` takes a single
 * `message` string, and `PaperTrading`'s `PanelBody` takes a single `message` that it prints
 * as a sentence in the notice body. Both used to be fed `extractErrorMessage(err, fallback)`
 * from `components/ui-legacy/primitives.jsx` — the function that fell through to
 * `JSON.stringify(detail)` and then `err.message`, which is how an axios message, a FastAPI
 * validation dump and a backend traceback reached the screen verbatim (Requirement 14.4).
 *
 * This is the one-line form of the same authored copy, so those call sites can be re-pointed
 * without inventing a second wording rule per file. It is a thin adapter over
 * `translateError` and reads nothing off the error itself — there is no branch here that
 * touches `error.message`, and there is nowhere for a stack to enter.
 *
 * THE RULE, IN ONE PLACE
 * ----------------------
 * `headline`, then `detail` when there is one. Both, not either:
 *
 *   * `headline` alone drops the action — `AUTH_ERROR`'s "Your session has expired" without
 *     "Sign in again to continue" tells a trader what happened and not what to do.
 *   * `detail` alone drops the subject — "This is not caused by anything you did. Please try
 *     again in a moment." does not say which read failed, and on a toast fired by a button
 *     press it does not say whether the press took effect.
 *
 * Every `headline` in `errorCopy.js` is a clause with no terminal punctuation and every
 * `detail` is one or more full sentences, so a full stop and a space is the whole join.
 *
 * `retryable`, `action` and `supportRef` are deliberately dropped rather than flattened into
 * the sentence: a retry belongs on a control, a next step belongs on a link, and a support
 * reference belongs somewhere selectable. A one-line string is none of those, and a caller
 * that needs them should render `ds/ErrorState` instead of this.
 *
 * @module design/errorLine
 */

import { translateError } from './errorCopy';

/**
 * The authored copy for `error`, as one string.
 *
 * Total over its input for the same reason `translateError` is: an `ApiError`, a
 * `subscription_refused` frame, a bare `Error`, a string, `null` and `undefined` all produce
 * a non-empty sentence, so there is no input that renders a blank toast or an empty notice.
 *
 * @param {*} error - Whatever was caught.
 * @param {string} [context] - A `CONTEXT_COPY` key naming the surface. It is what makes the
 *   last-resort copy specific, so pass the page's own key rather than omitting it.
 * @returns {string}
 */
export function errorLine(error, context) {
  const { headline, detail } = translateError(error, context);
  return detail ? `${headline}. ${detail}` : headline;
}

export default errorLine;
