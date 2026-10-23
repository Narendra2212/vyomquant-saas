/**
 * src/api/modules/legal.js — LEGAL-01
 *
 * Records which version of the legal documents a user accepted, and reports
 * whether their acceptance is current.
 *
 * WHY THIS MODULE EXISTS
 * ----------------------
 * The signup page already had a "I agree to the Terms and Risk Disclosure"
 * checkbox and refused to submit without it. That checkbox was React state: it
 * gated the local form and was never sent to Supabase Auth, to this API, or to
 * any table. With 182 existing accounts, the platform could not say which terms
 * any of them had agreed to. These three calls are the missing half.
 *
 * THE SERVER DOES NOT TRUST THE VERSION WE SEND
 * ---------------------------------------------
 * `accept()` posts the version this bundle displayed, and the server validates
 * it against its own constant before writing anything. A 409 with
 * `LEGAL_VERSION_UNKNOWN` means THIS BUNDLE IS STALE — the documents were
 * updated since it was built — and the correct response is to reload, not to
 * retry the same request.
 */

import { get, post } from '../../apiClient';

export const legalApi = {
  /**
   * The document version this deployment publishes.
   *
   * Unauthenticated. Used to detect that the running bundle is older than the
   * server's documents, in which case what the user is reading is not what the
   * server would record them as accepting.
   *
   * @returns {Promise<{version: string, effective_date: string, required_documents: string[]}>}
   */
  getCurrentVersion() {
    return get('/api/legal/current');
  },

  /**
   * This user's acceptance history and whether it covers the current version.
   *
   * `needs_acceptance` is the field to act on. It is true for every account
   * created before this mechanism existed, which is correct: no record exists,
   * so none of them can be shown to have accepted anything.
   *
   * Throws on 503 rather than returning a default. A read that did not happen
   * must NOT be reported as "already accepted", which would silently skip the
   * prompt for everyone during an outage.
   *
   * @returns {Promise<{current_version: string, accepted_version: string|null,
   *                    needs_acceptance: boolean, history: object[]}>}
   */
  getMyAcceptance() {
    return get('/api/legal/acceptance');
  },

  /**
   * Record acceptance of the current documents.
   *
   * Requires a live session, so it must be called AFTER authentication
   * completes — at signup that is after OTP verification, since the password
   * step deliberately tears down any session it created.
   *
   * Idempotent server-side: a unique index on (user_id, document_version) means
   * a double click or a retried request is success, not a conflict.
   *
   * @param {object}   params
   * @param {string}   params.version    the version displayed to the user
   * @param {string[]} params.documents  document ids shown; must be the full
   *                                     required set, as partial acceptance is
   *                                     refused rather than recorded
   * @param {'signup'|'re_acceptance'} [params.context]
   */
  accept({
    version,
    documents,
    context = 'signup',
    ageConfirmed,
    declaredCountry,
  }) {
    return post('/api/legal/accept', {
      version,
      documents,
      context,
      // LEGAL-01: the two representations Terms section 3 asks for. REQUIRED for
      // a signup and refused server-side if absent -- `age_confirmed` must be
      // true, and `declared_country` must be a two-letter code that is not on
      // the restricted list. Omitted keys are sent as undefined and dropped by
      // JSON serialisation, which is correct for a re-acceptance: that flow asks
      // the user to accept new TEXT, not to restate where they live.
      age_confirmed: ageConfirmed,
      declared_country: declaredCountry,
    });
  },
};

export default legalApi;
