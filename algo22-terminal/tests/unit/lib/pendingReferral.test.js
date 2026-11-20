/**
 * REF-ATTR-01: the referral link `/signup?ref=CODE` reaches POST /api/referral/claim.
 * Before, the signup page never read `ref`, so no issued link could credit anyone.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  PENDING_REFERRAL_KEY,
  capturePendingReferral,
  flushPendingReferral,
  readPendingReferral,
} from '../../../src/lib/pendingReferral';

describe('pendingReferral', () => {
  beforeEach(() => window.localStorage.clear());

  it('remembers a well-formed ref, upper-cased', () => {
    expect(capturePendingReferral('?ref=claudebda7')).toBe('CLAUDEBDA7');
    expect(window.localStorage.getItem(PENDING_REFERRAL_KEY)).toBe('CLAUDEBDA7');
  });

  it('ignores a missing or malformed ref', () => {
    expect(capturePendingReferral('')).toBeNull();
    expect(capturePendingReferral('?ref=<script>')).toBeNull();
    expect(readPendingReferral()).toBeNull();
  });

  it('claims once and forgets the code after an answer', async () => {
    capturePendingReferral('?ref=CODE123');
    const send = vi.fn().mockResolvedValue({ status: 'attributed' });
    await flushPendingReferral(send);
    expect(send).toHaveBeenCalledWith('CODE123');
    expect(readPendingReferral()).toBeNull();
    await flushPendingReferral(send);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('keeps the code when the claim fails transiently, drops it on a 4xx refusal', async () => {
    capturePendingReferral('?ref=CODE123');
    await flushPendingReferral(vi.fn().mockRejectedValue({ response: { status: 503 } }));
    expect(readPendingReferral()).toBe('CODE123');
    await flushPendingReferral(vi.fn().mockRejectedValue({ response: { status: 401 } }));
    expect(readPendingReferral()).toBe('CODE123');
    await flushPendingReferral(vi.fn().mockRejectedValue({ response: { status: 422 } }));
    expect(readPendingReferral()).toBeNull();
  });
});

describe('REF-UI-01: a typed referral code', () => {
  beforeEach(() => window.localStorage.clear());

  it('normalises, remembers and forgets a typed code', async () => {
    const m = await import('../../../src/lib/pendingReferral');
    expect(m.normaliseReferralCode(' vyom1234 ')).toBe('VYOM1234');
    expect(m.normaliseReferralCode('ab')).toBeNull();
    expect(m.setPendingReferral('vyom1234')).toBe('VYOM1234');
    expect(m.readPendingReferral()).toBe('VYOM1234');
    m.clearPendingReferral();
    expect(m.readPendingReferral()).toBeNull();
  });

  it('reports valid, invalid, and unknown (outage) separately', async () => {
    const { validateReferralCode } = await import('../../../src/lib/pendingReferral');
    expect(await validateReferralCode('GOOD1', vi.fn().mockResolvedValue({ valid: true, message: 'ok' })))
      .toEqual({ valid: true, message: 'ok' });
    expect((await validateReferralCode('BAD12', vi.fn().mockResolvedValue({ valid: false, message: 'Invalid referral code' }))).valid)
      .toBe(false);
    expect((await validateReferralCode('NET12', vi.fn().mockRejectedValue(new Error('offline')))).valid).toBeNull();
    expect((await validateReferralCode('x!', vi.fn())).valid).toBe(false);
  });
});
