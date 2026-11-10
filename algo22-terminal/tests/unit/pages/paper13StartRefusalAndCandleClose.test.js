/**
 * PAPER-13: a refused paper start named no reason and trapped the user; PAPER-12 follow-up: a
 * 1h session showed no price for up to an hour with no word on when one would appear.
 */
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import {
  classifyReadFailure,
  nextCandleCloseHint,
  startRefusalReason,
} from '../../../src/pages/paperTradingFormat';

// The shape the shared API client throws: the response body on `error.data`.
const refusal = (validation) => ({
  status: 409,
  data: {
    error: {
      code: 'PAPER_START_REFUSED',
      message: 'The paper session was not started.',
      details: { validation, symbol: 'BTC/USDT', timeframe: '1m' },
    },
  },
});

describe('a refused start says which check refused it', () => {
  it('names the timeframe mismatch in plain words', () => {
    const failure = classifyReadFailure(refusal('STRATEGY_SYMBOL_TIMEFRAME_INCOMPATIBLE'));
    expect(failure.message).toMatch(/built for a different market or timeframe/);
    expect(failure.validation).toBe('STRATEGY_SYMBOL_TIMEFRAME_INCOMPATIBLE');
  });

  it('still names an unknown check, by its code', () => {
    expect(startRefusalReason('SOMETHING_NEW')).toMatch(/SOMETHING_NEW/);
  });

  it('adds nothing for a failure that is not a start refusal', () => {
    expect(startRefusalReason(null)).toBeNull();
  });
});

describe('retrying a refused start goes back to the form', () => {
  const src = fs.readFileSync(
    path.resolve(__dirname, '../../../src/pages/PaperTrading.jsx'),
    'utf8',
  );

  it('clears the refusal instead of only re-reading the strategy list', () => {
    expect(src).toMatch(/onRetry=\{startRefusal \? \(\) => setStartRefusal\(null\) : loadStrategies\}/);
  });

  it('does not call a refused start a read', () => {
    expect(src).toMatch(/errorHeading=\{startRefusal \? 'The session was not started' : undefined\}/);
  });
});

describe('when the current candle closes', () => {
  const at = Date.UTC(2026, 10, 10, 14, 17, 30);

  it('1h closes on the next hour', () => {
    expect(nextCandleCloseHint('1h', at)).toMatch(/closes at 15:00 UTC/);
  });

  it('15m closes on the next quarter hour', () => {
    expect(nextCandleCloseHint('15m', at)).toMatch(/closes at 14:30 UTC/);
  });

  it('1d closes at midnight UTC', () => {
    expect(nextCandleCloseHint('1d', at)).toMatch(/closes at 00:00 UTC/);
  });

  it('an unreadable timeframe gives no hint rather than a guess', () => {
    expect(nextCandleCloseHint(undefined, at)).toBeNull();
    expect(nextCandleCloseHint('1w', at)).toBeNull();
  });
});
