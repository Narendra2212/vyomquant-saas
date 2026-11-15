/**
 * ST-UI-01 (2026-11-15), found on production Signal Trace with a running paper bot:
 *   1. every row said "Outcome: not available" -- the list returns `order_lifecycle_state`,
 *      the page read `status`;
 *   2. strategies showed as raw ids, never their names;
 *   3. the version printed as `vv1.0`.
 */
import { describe, expect, it } from 'vitest';
import { bareVersion, projectSignal } from '../../../src/lib/signalTraceRow';

const ROW = Object.freeze({
  id: 'sig_1',
  generated_at: '2026-11-15T07:27:02Z',
  strategy_id: 'e3ba0cbd',
  strategy_version: 'v1.0',
  symbol: 'SOL/USDT',
  decision: 'BUY',
  order_lifecycle_state: 'REJECTED',
});

describe('signalTraceRow.projectSignal', () => {
  it('reads the outcome from order_lifecycle_state, as the list returns it', () => {
    expect(projectSignal(ROW).outcome).toBe('REJECTED');
  });

  it('falls back to the legacy status only when there is no canonical state', () => {
    expect(projectSignal({ ...ROW, order_lifecycle_state: null, status: 'executed' }).outcome)
      .toBe('executed');
    expect(projectSignal({ ...ROW, order_lifecycle_state: null }).outcome).toBeNull();
  });

  it('names the strategy when its name is known, and keeps the id either way', () => {
    const named = projectSignal(ROW, { e3ba0cbd: 'Launch test 1m' });
    expect(named.strategy).toBe('e3ba0cbd');
    expect(named.strategyName).toBe('Launch test 1m');
    expect(projectSignal(ROW, {}).strategyName).toBeNull();
    expect(projectSignal(ROW).strategyName).toBeNull();
  });

  it('strips the server\'s leading v so the cell does not print vv1.0', () => {
    expect(projectSignal(ROW).version).toBe('1.0');
    expect(bareVersion('1.2')).toBe('1.2');
    expect(bareVersion('v2')).toBe('2');
    expect(bareVersion('vintage')).toBe('vintage');
    expect(bareVersion('')).toBeNull();
  });
});
