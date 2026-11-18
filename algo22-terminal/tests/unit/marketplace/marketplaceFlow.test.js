/** MKT-04: the pure rules behind the gated Marketplace screens. */
import { describe, expect, it } from 'vitest';
import {
  EVIDENCE_THRESHOLDS,
  MIN_LIVE_HISTORY_HOURS,
  adminActionsFor,
  canStartSubmission,
  currentSubmission,
  eligibilityFailureText,
  marketplaceRefusal,
  minorToText,
  priceToMinor,
  runShortfall,
  selectionProblem,
} from '../../../src/components/marketplace/marketplaceFlow';

const run = (over = {}) => ({
  status: 'completed',
  start_date: '2024-01-01T00:00:00+00:00',
  end_date: '2024-06-30T00:00:00+00:00',
  total_trades: 94,
  executed_bar_count: 4319,
  ...over,
});

describe('which backtests qualify as evidence', () => {
  it('a 6-month run with enough trades and bars qualifies', () => {
    expect(runShortfall(run())).toBeNull();
  });
  it('names the shortfall: window, trades, bars, incomplete', () => {
    expect(runShortfall(run({ end_date: '2024-02-01T00:00:00+00:00' }))).toMatch(/needs 90\+/);
    expect(runShortfall(run({ total_trades: 5 }))).toMatch(/5 trades; needs 20\+/);
    expect(runShortfall(run({ executed_bar_count: 10 }))).toMatch(/10 bars; needs 50\+/);
    expect(runShortfall(run({ status: 'failed' }))).toBe('Did not complete');
  });
  it('requires minConditions to maxConditions selected runs', () => {
    // MKT-07 moved the floor from 3 to 5. Read from EVIDENCE_THRESHOLDS rather than restated,
    // so the next move needs no edit here — and asserted on both sides of the boundary, which
    // is what makes it a bound rather than a number that appears in a message.
    const { minConditions, maxConditions } = EVIDENCE_THRESHOLDS;
    const ids = (n) => Array.from({ length: n }, (_, i) => String(i));

    expect(selectionProblem(ids(minConditions - 1))).toMatch(
      new RegExp(`at least ${minConditions}`),
    );
    expect(selectionProblem(ids(minConditions))).toBeNull();
    expect(selectionProblem(ids(maxConditions))).toBeNull();
    expect(selectionProblem(ids(maxConditions + 1))).toMatch(
      new RegExp(`at most ${maxConditions}`),
    );
  });

  it('states the live-history requirement the gate enforces', () => {
    // Task 15 D requirement 2. The constant is what the panel renders; the criterion itself is
    // decided server-side from `execution_records`.
    expect(MIN_LIVE_HISTORY_HOURS).toBe(24);
    expect(eligibilityFailureText('MP_LIVE_HISTORY')).toMatch(/traded live/);
    expect(eligibilityFailureText('MP_LIVE_HISTORY')).toMatch(/paper trading does not count/);
  });
});

describe('refusals are shown in the gate\'s own words', () => {
  it('maps a known criterion and still names an unknown one', () => {
    expect(eligibilityFailureText('EV_DISTINCT')).toMatch(/genuinely different conditions/);
    expect(eligibilityFailureText('NEW_CODE')).toMatch(/NEW_CODE/);
  });
  it('reads the marketplace envelope', () => {
    const r = marketplaceRefusal({
      status: 422,
      data: { error: { code: 'MARKETPLACE_ELIGIBILITY_FAILED', message: 'Not eligible.', details: { failures: ['EV_TRADES'] } } },
    });
    expect(r).toMatchObject({ status: 422, code: 'MARKETPLACE_ELIGIBILITY_FAILED', details: { failures: ['EV_TRADES'] } });
  });
});

describe('submission state', () => {
  it('the newest submission governs, and only a final one allows a new submission', () => {
    const subs = [
      { submission_state: 'REJECTED', created_at: '2026-01-01' },
      { submission_state: 'SUBMITTED', created_at: '2026-02-01' },
    ];
    expect(currentSubmission(subs).submission_state).toBe('SUBMITTED');
    expect(canStartSubmission(currentSubmission(subs))).toBe(false);
    expect(canStartSubmission({ submission_state: 'REJECTED' })).toBe(true);
    expect(canStartSubmission(null)).toBe(true);
  });
  it('admin actions follow the transition table', () => {
    expect(adminActionsFor('SUBMITTED')).toEqual(['approve', 'reject']);
    expect(adminActionsFor('APPROVED')).toEqual(['publish']);
    expect(adminActionsFor('PUBLISHED')).toEqual(['suspend', 'unpublish']);
    expect(adminActionsFor('UNPUBLISHED')).toEqual([]);
  });
});

describe('money is exact Minor_Units', () => {
  it('parses digit by digit, never through a float', () => {
    expect(priceToMinor('19.99', 'USD')).toEqual({ ok: true, minor: 1999 });
    expect(priceToMinor('0.29', 'USD')).toEqual({ ok: true, minor: 29 });
    expect(priceToMinor('19.999', 'USD').ok).toBe(false);
    expect(priceToMinor('0', 'USD').ok).toBe(false);
    expect(priceToMinor('abc', 'USD').ok).toBe(false);
    expect(priceToMinor('10', 'EUR').ok).toBe(false);
  });
  it('formats without float division', () => {
    expect(minorToText(1999, 'USD')).toBe('19.99 USD');
    expect(minorToText(5, 'INR')).toBe('0.05 INR');
  });
});
