/**
 * `design/reported` — vyomquant-ui-redesign task 6.4. design.md §18.
 * Requirements 14.5, 19.3.
 *
 * The property test for declared-field rendering is task 6.5's (Property 5). These are
 * the example cases that pin the accessor's totality and the one behaviour everything
 * else rests on: a zero is a reading and is never treated as an absence.
 */

import { describe, expect, it } from 'vitest';

import {
  UNREPORTED_REASON,
  available,
  fromNullable,
  isReadableValue,
  isReported,
  readReported,
  unavailable,
} from '../../../src/design/reported';

describe('reported: the two arms', () => {
  it('builds the available arm, zero included', () => {
    expect(available(12843.55)).toEqual({ available: true, value: 12843.55 });
    expect(available(0)).toEqual({ available: true, value: 0 });
    expect(available('BTC/USDT')).toEqual({ available: true, value: 'BTC/USDT' });
  });

  it('builds the unavailable arm with a reason, always', () => {
    expect(unavailable('The connector does not report unrealised P&L')).toEqual({
      available: false,
      reason: 'The connector does not report unrealised P&L',
    });
    // Requirement 19.3 wants a human reason; a blank one is not one.
    expect(unavailable().reason).toBe(UNREPORTED_REASON);
    expect(unavailable('   ').reason).toBe(UNREPORTED_REASON);
    expect(unavailable('  trimmed  ').reason).toBe('trimmed');
  });

  it('freezes both arms, so a consumer cannot turn an absence into a value', () => {
    expect(Object.isFrozen(available(1))).toBe(true);
    expect(Object.isFrozen(unavailable('why'))).toBe(true);
  });
});

describe('reported: what counts as a reading', () => {
  it('counts a measured zero and a false, which is the whole point', () => {
    expect(isReadableValue(0)).toBe(true);
    expect(isReadableValue(-0)).toBe(true);
    expect(isReadableValue(false)).toBe(true);
    expect(isReadableValue('0')).toBe(true);
  });

  it('does not count an absence, a blank, an unreadable figure or a non-scalar', () => {
    expect(isReadableValue(null)).toBe(false);
    expect(isReadableValue(undefined)).toBe(false);
    expect(isReadableValue('')).toBe(false);
    expect(isReadableValue('   ')).toBe(false);
    expect(isReadableValue(Number.NaN)).toBe(false);
    expect(isReadableValue(Number.POSITIVE_INFINITY)).toBe(false);
    expect(isReadableValue({ value: 1 })).toBe(false);
    expect(isReadableValue([1])).toBe(false);
    expect(isReadableValue(() => 1)).toBe(false);
  });
});

describe('reported: the type guard', () => {
  it('recognises both arms', () => {
    expect(isReported(available(1))).toBe(true);
    expect(isReported(unavailable('why'))).toBe(true);
    expect(isReported({ available: true, value: 0 })).toBe(true);
    expect(isReported({ available: false })).toBe(true);
  });

  it('treats a malformed available arm as a Reported rather than as a raw object', () => {
    // The safe direction: `readReported` resolves it to not-available. Classifying it
    // as a raw value would hand an object to React.
    expect(isReported({ available: true })).toBe(true);
    expect(readReported({ available: true }).available).toBe(false);
  });

  it('rejects everything that is not the union', () => {
    expect(isReported(null)).toBe(false);
    expect(isReported(12)).toBe(false);
    expect(isReported('LIVE')).toBe(false);
    expect(isReported([])).toBe(false);
    expect(isReported({ value: 12 })).toBe(false);
    expect(isReported({ available: 'true', value: 12 })).toBe(false);
  });
});

describe('reported: the accessor is total', () => {
  it('reads the available arm', () => {
    expect(readReported(available(12843.55))).toEqual({
      available: true, value: 12843.55, reason: null,
    });
  });

  it('reads a reported zero as available — never as an absence', () => {
    expect(readReported(available(0))).toEqual({ available: true, value: 0, reason: null });
    expect(readReported(0)).toEqual({ available: true, value: 0, reason: null });
  });

  it('prefers the unavailable arm\'s own reason over the caller\'s fallback', () => {
    const report = readReported(unavailable('Drawdown is not computed for paper accounts'), 'generic');
    expect(report).toEqual({
      available: false, value: null, reason: 'Drawdown is not computed for paper accounts',
    });
  });

  it('falls back to the caller\'s reason, then to the module\'s', () => {
    expect(readReported({ available: false }, 'The account has no backtest record').reason)
      .toBe('The account has no backtest record');
    expect(readReported(null).reason).toBe(UNREPORTED_REASON);
    expect(readReported(undefined).reason).toBe(UNREPORTED_REASON);
  });

  it('does not take a claimed value at its word', () => {
    // A producer saying "available" while carrying nothing would put `undefined` on
    // screen beside a currency symbol.
    expect(readReported({ available: true, value: null }).available).toBe(false);
    expect(readReported({ available: true, value: Number.NaN }).available).toBe(false);
    expect(readReported({ available: true, value: '' }).available).toBe(false);
  });

  it('accepts a raw value, so a page can adopt the union one field at a time', () => {
    expect(readReported('BTC/USDT').value).toBe('BTC/USDT');
    expect(readReported(Number.NaN).available).toBe(false);
    expect(readReported({ pnl: 1 }).available).toBe(false);
  });
});

describe('reported: fromNullable', () => {
  it('maps a nullable server field onto the union', () => {
    expect(fromNullable(0)).toEqual({ available: true, value: 0 });
    expect(fromNullable(null, 'Not reported by the exchange connector')).toEqual({
      available: false, reason: 'Not reported by the exchange connector',
    });
    expect(fromNullable(undefined).reason).toBe(UNREPORTED_REASON);
  });
});
