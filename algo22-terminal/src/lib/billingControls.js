/**
 * Which subscription control the billing panel offers, as a decision that can be asserted.
 *
 * WHY THIS IS NOT AN INLINE JSX CONDITION ANY MORE
 * ------------------------------------------------
 * BILL-CANCEL-01. The Cancel control used to be hidden by two extra conditions, and both of
 * them stranded customers:
 *
 *   * `!isPaymentFailed` -- a subscription at `past_due`/`payment_failed` is one the
 *     platform is still trying to collect on, and hiding Cancel there left the customer
 *     unable to stop their own billing from the product. Support was the only way out.
 *   * `cancelAtPeriodEnd.available` -- that field is `ABSENCE.UNREPORTED`, so an
 *     entitlements response that omitted `cancel_at_period_end` rendered NEITHER control
 *     and left the page with no cancellation path at all.
 *
 * `routers/billing.py::cancel_subscription` is deliberately NOT gated on AAL2 so that a
 * paying customer who never enrolled an MFA factor can always stop being charged, and
 * `test_cancel_subscription_is_not_gated_so_a_customer_can_always_stop_billing` enforces
 * that server-side. A UI that hides the only control calling it re-creates the same trap
 * somewhere the server-side guard cannot see it -- so the rule lives here, where a test can
 * reach it, instead of inside a JSX ternary.
 *
 * THE RULE, in one sentence: cancellation is offered on a paid plan unless the page
 * positively knows there is nothing left to cancel. Not knowing is not a reason to strand
 * someone in a subscription.
 */

/** What the panel may show. Mutually exclusive by construction. */
export const SUBSCRIPTION_CONTROL = Object.freeze({
  CANCEL: 'cancel',
  RESUME: 'resume',
  NONE: 'none',
});

/**
 * @param {Object} state
 * @param {boolean} state.planIsPaid Whether the current plan is a paid tier. A free plan has
 *   no billing to stop, so it gets no control.
 * @param {boolean} state.isCancelled The subscription is already `cancelled` -- nothing left
 *   to cancel, and resuming is what that state offers.
 * @param {boolean} state.cancellationIsScheduled The page POSITIVELY knows a cancellation is
 *   already pending, i.e. `cancel_at_period_end` was reported AND is true. An unreported
 *   field is `false` here, which is what keeps Cancel reachable when the server said nothing.
 * @returns {'cancel'|'resume'|'none'}
 */
export function subscriptionControlFor({ planIsPaid, isCancelled, cancellationIsScheduled }) {
  if (planIsPaid !== true) return SUBSCRIPTION_CONTROL.NONE;

  // A PENDING cancellation is the one thing `POST /api/billing/resume` reverses, so that is
  // the only state offering the undo.
  if (cancellationIsScheduled === true) return SUBSCRIPTION_CONTROL.RESUME;

  // An already-`cancelled` subscription gets neither, which is the behaviour this panel has
  // always had. Resume is NOT offered here on purpose: that endpoint reverses a pending
  // cancellation, and a subscription whose term has ended is not pending -- offering it
  // would be a control that answers with a refusal.
  if (isCancelled === true) return SUBSCRIPTION_CONTROL.NONE;

  return SUBSCRIPTION_CONTROL.CANCEL;
}

export default subscriptionControlFor;
