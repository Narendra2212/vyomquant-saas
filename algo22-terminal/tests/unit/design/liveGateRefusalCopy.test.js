/**
 * LIVE-COPY-01: the live-trading gate's three refusals must each explain themselves.
 *
 * WHAT WAS BROKEN
 * ---------------
 * `backend_app/core/live_execution_gate.py::assert_platform_live_trading_enabled` raises
 * exactly three refusal codes, and NONE of them had an entry in `CODE_COPY`:
 *
 *     LIVE_TRADING_DISABLED_BY_PLATFORM_MODE
 *     LIVE_TRADING_NOT_ENABLED
 *     LIVE_TENANT_NOT_ALLOWLISTED
 *
 * So a refused deployment fell through to the generic 403 copy — "This action is not
 * available on your account · contact support" — which names neither the restriction nor
 * a next step. The backend's own message says "this tenant is not in the operator-managed
 * live-trading allowlist. No order was sent", and none of that reached the customer.
 *
 * Part 3 of the brief: "Improve the UI to explain the restriction and provide an
 * appropriate next step for authorized users." It also requires that a rejected
 * deployment is never presented as a successful one, which the backend already
 * guarantees by raising before any row is written — these tests cover the copy half.
 */
import { describe, expect, it } from 'vitest';

import { CODE_COPY } from '../../../src/design/errorCopy.js';

/** Every code `assert_platform_live_trading_enabled` can raise. */
const GATE_CODES = [
  'LIVE_TRADING_DISABLED_BY_PLATFORM_MODE',
  'LIVE_TRADING_NOT_ENABLED',
  'LIVE_TENANT_NOT_ALLOWLISTED',
];

describe('live-trading gate refusals have their own copy', () => {
  it.each(GATE_CODES)('%s has an entry', (code) => {
    expect(CODE_COPY[code]).toBeDefined();
  });

  it.each(GATE_CODES)('%s says no order was sent', (code) => {
    // The single most important fact in a trading refusal, and the generic 403 copy did
    // not carry it: the customer needs to know nothing reached the exchange.
    expect(CODE_COPY[code].detail).toMatch(/no order was sent/i);
  });

  it.each(GATE_CODES)('%s is not offered as retryable', (code) => {
    // Retrying cannot change any of these verdicts — the allowlist is operator-managed
    // and the other two are deployment settings. A retry button on a safety refusal
    // invites the hammering that makes an incident worse.
    expect(CODE_COPY[code].retryable).toBe(false);
  });

  it.each(GATE_CODES)('%s says paper and backtesting still work', (code) => {
    // Otherwise the refusal reads as "your account is broken" rather than "this one
    // capability is gated", and the customer stops using the parts that do work.
    expect(CODE_COPY[code].detail).toMatch(/paper/i);
  });

  it('the allowlist refusal names a next step rather than just a cause', () => {
    const { detail } = CODE_COPY.LIVE_TENANT_NOT_ALLOWLISTED;
    expect(detail).toMatch(/contact support/i);
  });

  it('the allowlist refusal does not imply it will lift on its own', () => {
    const { detail, headline } = CODE_COPY.LIVE_TENANT_NOT_ALLOWLISTED;
    expect(`${headline} ${detail}`).not.toMatch(/try again|shortly|temporar|please wait/i);
  });

  it('the three refusals are distinguishable from one another', () => {
    // An operator reading a support ticket has to know WHICH setting to look at: the
    // platform mode, the live-trading flag, or the per-tenant allowlist. Identical copy
    // for all three would send them to the wrong one.
    const headlines = GATE_CODES.map((code) => CODE_COPY[code].headline);
    expect(new Set(headlines).size).toBe(GATE_CODES.length);
  });

  it('no refusal leaks who else is allowlisted', () => {
    const { detail } = CODE_COPY.LIVE_TENANT_NOT_ALLOWLISTED;
    expect(detail).not.toMatch(/tenant id|allowlist:|VYOMQUANT_/i);
  });
});
