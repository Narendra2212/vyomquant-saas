/**
 * BILL-CANCEL-01 — a customer can always stop their own billing.
 *
 * REPORTED AS "the cancellation button is not there; people have to contact support".
 * It was true in two states, and both were the UI re-creating a trap the backend
 * deliberately refuses to create.
 *
 * `routers/billing.py::cancel_subscription` is NOT gated on AAL2 on purpose: `require_aal2`
 * is a hard 403 for any `aal1` session and MFA enrolment is not required to hold an account,
 * so gating it would mean a paying customer who never enrolled a factor could not stop being
 * charged. The route's docstring calls that a consumer-protection harm and a dark pattern,
 * and
 * `tests/test_sec15_billing_mutations_require_aal2.py::test_cancel_subscription_is_not_gated_so_a_customer_can_always_stop_billing`
 * keeps a later change from adding the gate.
 *
 * None of that helps if the UI hides the only control that calls the route. It did, twice:
 *
 *   1. `!isPaymentFailed` -- hidden at `past_due`/`payment_failed`, which is exactly when a
 *      customer wants out and exactly when the platform is still trying to collect.
 *   2. `cancelAtPeriodEnd.available` -- that field is `ABSENCE.UNREPORTED`, so an
 *      entitlements response omitting `cancel_at_period_end` rendered NEITHER control.
 *
 * This file is the front-end half of the server-side guard, and it is a unit test rather
 * than a page render because the rule is the thing worth pinning: a server-side test cannot
 * see a button that was never drawn.
 */

import { describe, it, expect } from 'vitest';

import { SUBSCRIPTION_CONTROL, subscriptionControlFor } from '../../../src/lib/billingControls';

/** An active paid subscription with nothing pending. */
const ACTIVE = Object.freeze({
  planIsPaid: true,
  isCancelled: false,
  cancellationIsScheduled: false,
});

describe('subscriptionControlFor', () => {
  it('offers cancellation on an active paid plan', () => {
    expect(subscriptionControlFor(ACTIVE)).toBe(SUBSCRIPTION_CONTROL.CANCEL);
  });

  it('offers the undo once a cancellation is scheduled', () => {
    expect(
      subscriptionControlFor({ ...ACTIVE, cancellationIsScheduled: true }),
    ).toBe(SUBSCRIPTION_CONTROL.RESUME);
  });

  it('offers nothing on a free plan, which has no billing to stop', () => {
    expect(subscriptionControlFor({ ...ACTIVE, planIsPaid: false })).toBe(
      SUBSCRIPTION_CONTROL.NONE,
    );
  });

  it('offers nothing once the subscription has actually ended', () => {
    // `POST /api/billing/resume` reverses a PENDING cancellation; a term that has ended is
    // not pending, so a Resume control here would answer with a refusal.
    expect(subscriptionControlFor({ ...ACTIVE, isCancelled: true })).toBe(
      SUBSCRIPTION_CONTROL.NONE,
    );
  });

  // -------------------------------------------------------------------------
  // The two regressions. These are the reason this file exists.
  // -------------------------------------------------------------------------

  it('still offers cancellation when the payment is failing', () => {
    /*
     * The defect, stated as the user hit it: a subscription being dunned showed no way to
     * cancel. Payment state is NOT an input to this decision at all -- which is the fix,
     * because any branch on it is a branch that can hide the control from someone being
     * charged. Passing it is harmless and changes nothing.
     */
    expect(
      subscriptionControlFor({ ...ACTIVE, isPaymentFailed: true, status: 'past_due' }),
    ).toBe(SUBSCRIPTION_CONTROL.CANCEL);
    expect(
      subscriptionControlFor({ ...ACTIVE, isPaymentFailed: true, status: 'payment_failed' }),
    ).toBe(SUBSCRIPTION_CONTROL.CANCEL);
  });

  it('still offers cancellation when the server did not report the pending flag', () => {
    /*
     * `cancel_at_period_end` is ABSENCE.UNREPORTED, so "absent" is a real case and used to
     * render nothing. Not knowing whether a cancellation is pending is not a reason to
     * strand someone in a subscription: the server is authoritative and will say so.
     */
    expect(subscriptionControlFor({ planIsPaid: true })).toBe(SUBSCRIPTION_CONTROL.CANCEL);
    expect(
      subscriptionControlFor({
        planIsPaid: true,
        isCancelled: undefined,
        cancellationIsScheduled: undefined,
      }),
    ).toBe(SUBSCRIPTION_CONTROL.CANCEL);
  });

  it('never answers with both controls at once', () => {
    const states = [
      ACTIVE,
      { ...ACTIVE, cancellationIsScheduled: true },
      { ...ACTIVE, isCancelled: true },
      { ...ACTIVE, planIsPaid: false },
      { planIsPaid: true },
    ];
    for (const state of states) {
      expect(Object.values(SUBSCRIPTION_CONTROL)).toContain(subscriptionControlFor(state));
    }
  });
});
