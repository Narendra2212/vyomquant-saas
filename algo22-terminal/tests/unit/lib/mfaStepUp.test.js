/**
 * `lib/mfaStepUp.js` — the `require_aal2` refusal, and the one action it must never guard.
 *
 * MFA-STEPUP-02. `pages/Billing.jsx` calls `POST /api/billing/resume`, which carries
 * `Depends(require_aal2)` (`routers/billing.py::resume_subscription`, SEC-15). A session at
 * `aal1` is therefore refused with a 403 the customer CAN clear — but Billing had no
 * interception for it, so the refusal landed in the notice band as a flat failure and the
 * customer was told resuming did not work, with no way to do what the server was asking.
 * `pages/ExchangeManager.jsx` already ran that challenge in place for the identical refusal.
 *
 * Two things are pinned here.
 *
 * 1. THE PREDICATE, against both error shapes. `apiClient` rejects with an `ApiError`
 *    carrying `status`/`data` as own properties; a direct axios caller uses the nested
 *    `response` shape. Reading only one is how a real refusal goes unrecognised and the
 *    step-up silently never opens.
 *
 * 2. THAT CANCELLATION IS NOT GUARDED, which is a consumer-protection decision and not an
 *    oversight. `require_aal2` is a hard 403 for any `aal1` session and MFA enrolment is not
 *    required to hold an account, so gating `POST /api/billing/cancel` would mean a paying
 *    customer who never enrolled a factor COULD NOT STOP THEIR OWN BILLING without
 *    contacting support. The backend records that reasoning and
 *    `tests/test_sec15_billing_mutations_require_aal2.py::test_cancel_subscription_is_not_gated_so_a_customer_can_always_stop_billing`
 *    keeps a later change from adding it. This is the front-end half of that guard: a
 *    step-up dialog in front of cancellation would be a challenge the server never asks
 *    for, and the dark pattern the backend decision exists to avoid.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, it, expect } from 'vitest';

import { isMfaStepUpRequired } from '../../../src/lib/mfaStepUp';

describe('isMfaStepUpRequired', () => {
  it('recognises the shape apiClient rejects with', () => {
    expect(
      isMfaStepUpRequired({
        status: 403,
        data: { detail: 'Two-factor authentication is required for this operation.' },
      }),
    ).toBe(true);
  });

  it('recognises the nested axios response shape', () => {
    expect(
      isMfaStepUpRequired({
        response: { status: 403, data: { detail: 'MFA required (aal2).' } },
      }),
    ).toBe(true);
  });

  it('does not fire on a 403 that is about something else', () => {
    // A plan-entitlement refusal is also a 403, and it is NOT clearable by verifying.
    // Offering a step-up for it would send the customer to an authenticator to fix a
    // subscription-tier problem.
    expect(
      isMfaStepUpRequired({ status: 403, data: { detail: 'Your plan does not include this.' } }),
    ).toBe(false);
  });

  it('does not fire on other statuses, however the detail reads', () => {
    expect(
      isMfaStepUpRequired({ status: 500, data: { detail: 'mfa' } }),
    ).toBe(false);
    expect(
      isMfaStepUpRequired({ status: 401, data: { detail: 'two-factor' } }),
    ).toBe(false);
  });

  it('survives the shapes that carry no detail at all', () => {
    expect(isMfaStepUpRequired(null)).toBe(false);
    expect(isMfaStepUpRequired(undefined)).toBe(false);
    expect(isMfaStepUpRequired({})).toBe(false);
    expect(isMfaStepUpRequired({ status: 403 })).toBe(false);
    expect(isMfaStepUpRequired({ status: 403, data: { detail: { mfa: true } } })).toBe(false);
  });
});

describe('where Billing wires the step-up', () => {
  const source = readFileSync(
    path.join(process.cwd(), 'src', 'pages', 'Billing.jsx'),
    'utf8',
  );

  /** The body of a named arrow-function handler on the page. */
  const handler = (name) => {
    const start = source.indexOf(`const ${name} = async () => {`);
    expect(start, `${name} not found in Billing.jsx`).toBeGreaterThan(-1);
    // Up to the start of the next top-level handler, which is enough to scope a catch.
    const rest = source.slice(start + 1);
    const next = rest.indexOf('\n  const handle');
    return next === -1 ? rest : rest.slice(0, next);
  };

  it('offers the step-up when resuming is refused for AAL2', () => {
    expect(handler('handleResume')).toContain('isMfaStepUpRequired');
  });

  it('never offers it for cancellation, so a customer can always stop billing', () => {
    /*
     * The front-end half of SEC-15's cancellation decision. If this ever fails, read
     * `routers/billing.py::cancel_subscription` before "fixing" it: the route is ungated on
     * purpose, so there is no refusal here to clear, and a dialog in front of cancelling
     * would block a customer with no enrolled factor from stopping their own payments.
     */
    expect(handler('handleCancel')).not.toContain('isMfaStepUpRequired');
    expect(handler('handleCancel')).not.toContain('setStepUp');
  });
});
