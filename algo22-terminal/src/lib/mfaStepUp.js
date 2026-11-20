/**
 * The `require_aal2` refusal, recognised in ONE place.
 *
 * WHY THIS IS A MODULE AND NOT A FUNCTION IN A PAGE
 * -------------------------------------------------
 * It began as a local helper in `pages/ExchangeManager.jsx`, whose own comment stated the
 * rule this file now enforces: "ONE definition ... These were about to be two copies of the
 * same predicate, which is how one of them comes to recognise a refusal the other does not."
 *
 * `pages/Billing.jsx` then needed the same predicate, because `POST /api/billing/resume`
 * carries the same `require_aal2` dependency that the exchange-key writes do
 * (`routers/billing.py::resume_subscription`, SEC-15). Copying it there would have been the
 * second copy that comment warns about — on an authentication boundary, where the failure
 * mode is a page that silently cannot offer a step-up for a refusal it does not recognise.
 *
 * WHICH ROUTES THIS APPLIES TO, AND ONE IT DELIBERATELY DOES NOT
 * -------------------------------------------------------------
 * Gated on AAL2 (SEC-15's rule is the direction of the money, not the word "billing"):
 *
 *   * `POST /api/billing/resume`            — resumes billing; an attacker here costs money
 *   * `DELETE /api/billing/payment-methods/{id}`
 *   * the exchange-key writes (AUTH-01)
 *
 * NOT gated, on purpose: `POST /api/billing/cancel`. `require_aal2` is a hard 403 for any
 * session at `aal1`, and MFA enrolment is not required to hold an account — so gating
 * cancellation would mean a paying customer who never enrolled a factor could not stop
 * their own billing without contacting support. `routers/billing.py::cancel_subscription`
 * records that reasoning, and
 * `tests/test_sec15_billing_mutations_require_aal2.py::test_cancel_subscription_is_not_gated_so_a_customer_can_always_stop_billing`
 * keeps a later well-meaning change from adding it. A caller must therefore NOT wire a
 * step-up onto cancellation: there is no refusal to clear, and the dialog would be a
 * challenge in front of an action the server never challenges.
 */

/**
 * Is this the `require_aal2` refusal — the one a user can clear by verifying?
 *
 * Both error shapes are read because `apiClient` rejects with an `ApiError` carrying
 * `status`/`data` as own properties, while a direct axios caller (and some page tests) use
 * the nested `response` shape. Reading only one is how a real refusal goes unrecognised.
 *
 * The detail match is a regex over the server's sentence rather than a code, because
 * `core/dependencies.py::require_aal2` answers with a plain string and no `error` code. If
 * that ever becomes structured, this is the single place that learns about it.
 *
 * @param {unknown} err
 * @returns {boolean}
 */
export function isMfaStepUpRequired(err) {
  const status = err?.status ?? err?.response?.status;
  const detail = err?.data?.detail ?? err?.response?.data?.detail;
  return status === 403 && typeof detail === 'string' && /two-factor|mfa/i.test(detail);
}

export default isMfaStepUpRequired;
