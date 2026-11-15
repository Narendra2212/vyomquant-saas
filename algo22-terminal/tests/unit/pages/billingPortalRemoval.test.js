/**
 * ═══════════════════════════════════════════════════════════════════════════
 * billingPortalRemoval.test.js — BILLING-PORTAL-02
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * WHAT WAS REMOVED AND WHY
 * ---------------------------------------------------------------------------
 * The Billing & plan page carried two controls that could not succeed:
 *
 *   *Manage billing*          current-plan panel's `actions`, behind `planIsPaid`
 *   *Update payment method*   the `past_due` / `payment_failed` alert's `action`
 *
 * Both called `POST /api/billing/portal`. That endpoint answers
 * `501 PROVIDER_HAS_NO_BILLING_PORTAL` (`backend_app/routers/billing.py`) for every caller:
 * Razorpay is the only gateway and publishes no hosted self-serve billing portal, and the
 * branch that once created a `stripe.billing_portal.Session` went with Stripe. So a paid
 * subscriber's only possible outcome from *Manage billing* was an error toast, and the one
 * person with an urgent reason to press *Update payment method* — someone whose charge had
 * just failed — got the same.
 *
 * An earlier pass (BILLING-PORTAL-01) removed a third portal button from the payment-methods
 * panel and deliberately kept `handleOpenPortal` "for the two call sites outside that panel",
 * on the grounds that they surfaced the server's own structured refusal rather than a generic
 * failure. Good copy on a refusal is still a refusal. This pass removed the remaining two.
 *
 * THE DISTINCTION THIS FILE EXISTS TO HOLD
 * ---------------------------------------------------------------------------
 * "Remove the control" is not "remove the payment provider integration", and the difference
 * is easy to lose in a later cleanup. So this file asserts BOTH halves:
 *
 *   - the portal control and its client wrapper are gone, and
 *   - checkout, currency, cancel and resume are still there.
 *
 * A test that only checked the first half would be satisfied by deleting billing entirely.
 *
 * The BACKEND route is intentionally untouched and is not asserted here — this is a frontend
 * suite and cannot import Python. It stays because it is still the honest answer to any
 * client that asks, including older desktop builds that have not been updated.
 */

import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

import { SRC, stripComments } from '../guards/source-scan.js';

/**
 * Comments stripped, STRING LITERALS KEPT.
 *
 * Both matter here. The prose in `Billing.jsx` and `api/modules/billing.js` discusses
 * `openPortal` and `Manage billing` at length — deliberately, so the next reader learns why
 * they are absent — so an un-stripped scan would find the very names it is checking for.
 * And `codeOnly()` is the wrong tool in the other direction: it masks string spans, and a
 * button label is a string, so `'Manage billing'` would vanish along with the comments.
 */
const code = (relative) => stripComments(fs.readFileSync(path.join(SRC, relative), 'utf8'));

const BILLING_PAGE = 'pages/Billing.jsx';
const BILLING_API = 'api/modules/billing.js';

describe('BILLING-PORTAL-02: the dead portal controls are gone', () => {
  it('Billing.jsx has no portal handler', () => {
    expect(code(BILLING_PAGE)).not.toMatch(/\bhandleOpenPortal\b/);
  });

  it('Billing.jsx has no portal loading state', () => {
    expect(code(BILLING_PAGE)).not.toMatch(/\bisPortalLoading\b/);
  });

  it('Billing.jsx never calls the portal endpoint', () => {
    const source = code(BILLING_PAGE);
    expect(source).not.toMatch(/api\.billing\.openPortal/);
    expect(
      source,
      'the page must not reach the portal route by any spelling, including a hand-rolled post',
    ).not.toMatch(/billing\/portal/);
  });

  it.each([
    ['Manage billing', /Manage billing/],
    ['Update payment method', /Update payment method/],
  ])('Billing.jsx renders no %s control', (_label, pattern) => {
    expect(
      code(BILLING_PAGE),
      'this label can only belong to a control that opens the portal, which answers 501',
    ).not.toMatch(pattern);
  });

  it('the client API module exposes no openPortal', () => {
    expect(
      code(BILLING_API),
      'a client method whose only contract is to reject is a trap for the next caller',
    ).not.toMatch(/^\s*openPortal\s*:/m);
  });
});

describe('BILLING-PORTAL-02: the provider integration is intact', () => {
  /*
    The guard above, alone, would be satisfied by deleting the billing module. These four are
    the capabilities that DO reach working endpoints, and removing the portal button was never
    allowed to touch them.
  */
  it.each(['createCheckout', 'setCurrency', 'cancelSubscription', 'resumeSubscription'])(
    'api/modules/billing.js still exposes %s',
    (method) => {
      expect(code(BILLING_API)).toMatch(new RegExp(`^\\s*${method}\\s*:`, 'm'));
    },
  );

  it('the page still runs a checkout', () => {
    expect(code(BILLING_PAGE)).toMatch(/\bhandleCheckout\b/);
  });

  it.each(['handleCancel', 'handleResume'])('the page still offers %s', (handler) => {
    expect(code(BILLING_PAGE)).toMatch(new RegExp(`\\b${handler}\\b`));
  });
});

describe('BILLING-PORTAL-02: the payment-failed alert still warns', () => {
  /*
    Removing the button must not remove the warning. The alert is driven by the server's own
    `past_due` / `payment_failed` status and is the only place the account learns that access
    is suspended; what changed is that its body now states the remedy that exists (pay again
    at Razorpay Checkout, choosing a different instrument) instead of offering a control that
    could only fail.
  */
  it('the alert is still rendered for a failed payment', () => {
    const source = code(BILLING_PAGE);
    expect(source).toMatch(/\bisPaymentFailed\b/);
    expect(source).toMatch(/Payment failed/);
  });

  it('the alert explains where the instrument is actually changed', () => {
    expect(
      code(BILLING_PAGE),
      'with the control gone the sentence is the whole remedy, so it has to name Razorpay '
        + 'Checkout rather than tell the user to update something that is not stored',
    ).toMatch(/Razorpay Checkout/);
  });
});
