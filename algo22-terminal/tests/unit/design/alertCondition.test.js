/**
 * @fileoverview `design/alertCondition` — the three arms, and the three outcomes.
 *
 * vyomquant-ui-redesign task 19.2 part A. design.md §7.1. Requirements 3.3, 14.5, 19.3.
 *
 * WHAT IS PINNED HERE
 * ===================
 *   1. **Each arm fires on its own inputs and on nothing else.** Three arms, three
 *      independent cases, then all three together — because a disjunction that only works
 *      when one term is true is not a disjunction.
 *   2. **The three outcomes are distinguishable.** `{evaluated: true, firing: false}` and
 *      `{evaluated: false, firing: false}` agree on `firing` and mean opposite things: the
 *      first is "checked, nothing held", the second is "nothing was readable, so nothing is
 *      known". A caller that could not tell them apart would render an absence as an
 *      all-clear, which is what Requirement 14.5 forbids and what `absence: UNMEASURABLE`
 *      on the `pageFields` entry exists to prevent.
 *   3. **Totality.** A non-array, a `null`, a number where a record belongs, a numeric
 *      status and a blank one all produce an answer. A derivation that throws takes the
 *      Dashboard down on a payload shape nobody predicted.
 *   4. **The exchange arm does not fire on the real payload.** `pageFields`' note records
 *      that `dashboard_aggregation_service.get_exchange_health` writes the literal
 *      `"connected"` for every venue, so the arm reads a value that cannot satisfy it. The
 *      arm stays implemented; what is asserted is that today's server produces no finding
 *      from it, and that its silence is never phrased as one.
 *   5. **`summary` names fired arms only.** No absence of a condition is ever stated as a
 *      finding, which is how the constant above is handled honestly: the phrase "all
 *      exchanges connected" is not constructible from this return value.
 */

import { describe, it, expect } from 'vitest';

import {
  ALERT_ARMS,
  ARM_EXCHANGE,
  ARM_EXECUTION,
  ARM_STRATEGY,
  EXECUTION_FAILURE_STATES,
  SUMMARY_SEPARATOR,
  armDetail,
  deriveAlertCondition,
} from '../../../src/design/alertCondition';

/** One arm out of the three, by id. */
const arm = (condition, id) => condition.arms.find((entry) => entry.arm === id);

/**
 * `exchange.exchanges[]` exactly as `get_exchange_health` publishes it.
 *
 * Both constants included — `status: "connected"` and `latency_ms: 35` — because the point
 * of case 4 above is that this is the REAL shape, not a shape invented to make the arm quiet.
 */
const venue = (exchangeId, status = 'connected') => ({
  exchange_id: exchangeId,
  status,
  latency_ms: 35,
  last_sync: '2026-08-26T12:00:00Z',
});

const strategy = (name, status) => ({ id: `strat_${name}`, name, status });

const execution = (id, status) => ({ id, symbol: 'BTC/USDT', status });

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE THREE ARMS, INDEPENDENTLY
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('deriveAlertCondition — the exchange arm', () => {
  it('fires on a venue whose status is not in the connected group', () => {
    const condition = deriveAlertCondition({ exchanges: [venue('binance', 'disconnected')] });

    expect(condition.firing).toBe(true);
    expect(condition.evaluated).toBe(true);
    expect(condition.firedArms).toEqual([ARM_EXCHANGE]);
    expect(condition.summary).toBe('1 exchange not connected');

    const exchangeArm = arm(condition, ARM_EXCHANGE);
    expect(exchangeArm.count).toBe(1);
    expect(exchangeArm.armReported).toBe(1);
    expect(exchangeArm.states).toEqual(['disconnected']);
    expect(exchangeArm.subjects).toEqual(['binance']);
  });

  it('does not fire on the connected group\'s other spellings', () => {
    // §4.1 resolves `connected`, `paired`, `open` and `ok` to one group. A venue reporting
    // `ok` announced as disconnected would be a claim about the link nothing checked.
    const condition = deriveAlertCondition({
      exchanges: [venue('a', 'connected'), venue('b', 'paired'), venue('c', 'ok'), venue('d', 'open')],
    });

    expect(condition.firing).toBe(false);
    expect(condition.evaluated).toBe(true);
    expect(arm(condition, ARM_EXCHANGE).armReported).toBe(4);
    expect(arm(condition, ARM_EXCHANGE).count).toBe(0);
  });

  it('DOES NOT FIRE on the constant `"connected"` payload the aggregation service really sends', () => {
    // `pageFields`' `exchangeHealth` / `alertCondition` notes: every entry, every account,
    // from no measurement. So on today's server this arm can produce no finding.
    const condition = deriveAlertCondition({
      exchanges: [venue('binance'), venue('bybit'), venue('kraken')],
      strategies: [],
      executions: [],
    });

    expect(condition.firing).toBe(false);
    // Read, and read as connected — this is not the unevaluable case.
    expect(condition.evaluated).toBe(true);
    expect(condition.reported).toBe(3);
    expect(arm(condition, ARM_EXCHANGE).fired).toBe(false);
    // And the silence is not phrased as a finding. The arm has no sentence at all.
    expect(arm(condition, ARM_EXCHANGE).summary).toBeNull();
    expect(condition.summary).toBeNull();
  });
});

describe('deriveAlertCondition — the strategy arm', () => {
  it('fires on §4.1\'s error group', () => {
    const condition = deriveAlertCondition({ strategies: [strategy('RSI Reversion', 'error')] });

    expect(condition.firedArms).toEqual([ARM_STRATEGY]);
    expect(condition.summary).toBe('1 strategy in an error state');
    expect(arm(condition, ARM_STRATEGY).subjects).toEqual(['RSI Reversion']);
  });

  it('fires on `crashed`, which is the stopped-on-error case §4.1 has no spelling for', () => {
    // `_STATUS_TO_BINDING_STATE` maps `crashed` to `FAILED`; the §4.1 vocabulary does not
    // carry the word at all, so the union of the two sources is what makes this fire.
    const condition = deriveAlertCondition({ strategies: [strategy('BTC Trend', 'crashed')] });

    expect(condition.firing).toBe(true);
    expect(arm(condition, ARM_STRATEGY).states).toEqual(['crashed']);
  });

  it('does not fire on a resting state a trader asked for', () => {
    // `stopped`, `cancelled` and `completed` all map to `STOPPED` — a resting state, not a
    // health verdict. A strategy a trader stopped is not an alert condition.
    const condition = deriveAlertCondition({
      strategies: [
        strategy('a', 'running'),
        strategy('b', 'paused'),
        strategy('c', 'stopped'),
        strategy('d', 'cancelled'),
        strategy('e', 'completed'),
      ],
    });

    expect(condition.firing).toBe(false);
    expect(arm(condition, ARM_STRATEGY).armReported).toBe(5);
    expect(arm(condition, ARM_STRATEGY).count).toBe(0);
  });

  it('counts every firing strategy and pluralises the phrase', () => {
    const condition = deriveAlertCondition({
      strategies: [strategy('a', 'crashed'), strategy('b', 'failed'), strategy('c', 'running')],
    });

    expect(condition.summary).toBe('2 strategies in an error state');
    expect(arm(condition, ARM_STRATEGY).subjects).toEqual(['a', 'b']);
    // Distinct states, in first-seen order — not one entry per firing row.
    expect(arm(condition, ARM_STRATEGY).states).toEqual(['crashed', 'failed']);
  });
});

describe('deriveAlertCondition — the execution arm', () => {
  it('fires on failed and on rejected, and on nothing else in §4.1\'s error group', () => {
    for (const state of EXECUTION_FAILURE_STATES) {
      expect(deriveAlertCondition({ executions: [execution('x', state)] }).firing).toBe(true);
    }

    // `closed`, `blocked`, `stale` and `disconnected` are all in the same §4.1 group and
    // none of them is a failed or rejected submission, which is what the declaration says.
    const condition = deriveAlertCondition({
      executions: [
        execution('a', 'closed'),
        execution('b', 'blocked'),
        execution('c', 'stale'),
        execution('d', 'disconnected'),
        execution('e', 'filled'),
      ],
    });
    expect(condition.firing).toBe(false);
    expect(arm(condition, ARM_EXECUTION).armReported).toBe(5);
  });

  it('reads the status case-insensitively and reports it lowercased', () => {
    const condition = deriveAlertCondition({ executions: [execution('ord_1', 'REJECTED')] });

    expect(condition.firing).toBe(true);
    expect(arm(condition, ARM_EXECUTION).states).toEqual(['rejected']);
    expect(condition.summary).toBe('1 execution failed or rejected');
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * ALL THREE AT ONCE, AND THE ORDER THE SUMMARY READS IN
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('deriveAlertCondition — the whole disjunction', () => {
  it('reports all three arms in declaration order when all three fire', () => {
    const condition = deriveAlertCondition({
      exchanges: [venue('binance', 'disconnected'), venue('bybit', 'connected')],
      strategies: [strategy('BTC Trend', 'crashed'), strategy('idle one', 'stopped')],
      executions: [execution('ord_1', 'rejected'), execution('ord_2', 'filled')],
    });

    expect(condition.firing).toBe(true);
    expect(condition.evaluated).toBe(true);
    expect(condition.reported).toBe(6);
    expect(condition.firedArms).toEqual([ARM_EXCHANGE, ARM_STRATEGY, ARM_EXECUTION]);
    expect(condition.summary).toBe([
      '1 exchange not connected',
      '1 strategy in an error state',
      '1 execution failed or rejected',
    ].join(SUMMARY_SEPARATOR));
  });

  it('always returns all three arms, in `ALERT_ARMS` order, whether or not they fired', () => {
    // A caller reports what an arm SAW, not only what it found, so an unfired arm still has
    // an entry with its `armReported` count on it.
    const condition = deriveAlertCondition({ strategies: [strategy('a', 'error')] });

    expect(condition.arms.map((entry) => entry.arm)).toEqual([...ALERT_ARMS]);
    expect(arm(condition, ARM_EXCHANGE).armReported).toBe(0);
    expect(arm(condition, ARM_EXECUTION).armReported).toBe(0);
  });

  it('freezes what it returns, all the way down', () => {
    const condition = deriveAlertCondition({ strategies: [strategy('a', 'error')] });

    expect(Object.isFrozen(condition)).toBe(true);
    expect(Object.isFrozen(condition.arms)).toBe(true);
    expect(Object.isFrozen(condition.arms[0])).toBe(true);
    expect(Object.isFrozen(condition.firedArms)).toBe(true);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * `summary` NAMES FIRED ARMS ONLY
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('deriveAlertCondition — no absence is ever stated as a finding', () => {
  /** The word each arm's phrase is built on, so an arm's presence in prose is decidable. */
  const WORD = Object.freeze({
    [ARM_EXCHANGE]: 'exchange',
    [ARM_STRATEGY]: 'strateg',
    [ARM_EXECUTION]: 'execution',
  });

  const cases = [
    ['only the exchange arm', { exchanges: [venue('binance', 'disconnected')], strategies: [strategy('a', 'running')], executions: [execution('x', 'filled')] }],
    ['only the strategy arm', { exchanges: [venue('binance')], strategies: [strategy('a', 'crashed')], executions: [execution('x', 'filled')] }],
    ['only the execution arm', { exchanges: [venue('binance')], strategies: [strategy('a', 'running')], executions: [execution('x', 'failed')] }],
    ['two of the three', { exchanges: [venue('binance', 'connecting')], strategies: [strategy('a', 'running')], executions: [execution('x', 'failed')] }],
  ];

  for (const [name, input] of cases) {
    it(`mentions no unfired arm when ${name} fired`, () => {
      const condition = deriveAlertCondition(input);
      expect(condition.summary).not.toBeNull();

      for (const entry of condition.arms) {
        if (entry.fired) {
          expect(condition.summary).toContain(WORD[entry.arm]);
        } else {
          // The whole point: an arm that did not fire contributes no sentence, so the
          // summary cannot be read as a positive statement about it.
          expect(entry.summary).toBeNull();
          expect(condition.summary).not.toContain(WORD[entry.arm]);
        }
      }
    });
  }

  it('has no summary at all when nothing fired', () => {
    const condition = deriveAlertCondition({
      exchanges: [venue('binance')],
      strategies: [strategy('a', 'running')],
      executions: [execution('x', 'filled')],
    });

    expect(condition.summary).toBeNull();
    expect(condition.firedArms).toEqual([]);
    for (const entry of condition.arms) expect(entry.summary).toBeNull();
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE THIRD OUTCOME — `evaluated: false` IS NOT AN ALL-CLEAR
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('deriveAlertCondition — nothing readable is not the same as nothing wrong', () => {
  it('separates "read, none fired" from "nothing was readable"', () => {
    const allClear = deriveAlertCondition({
      exchanges: [venue('binance')],
      strategies: [strategy('a', 'running')],
      executions: [execution('x', 'filled')],
    });
    const unmeasurable = deriveAlertCondition({ exchanges: [], strategies: [], executions: [] });

    // They agree on `firing` — the disjunction over an empty set is false, the same way `0`
    // is not `null` — and `evaluated` is the only thing that tells them apart.
    expect(allClear.firing).toBe(false);
    expect(unmeasurable.firing).toBe(false);

    expect(allClear.evaluated).toBe(true);
    expect(allClear.reported).toBe(3);

    expect(unmeasurable.evaluated).toBe(false);
    expect(unmeasurable.reported).toBe(0);
  });

  it('is unevaluable when no arm read a single status, however the lists are shaped', () => {
    const inputs = [
      undefined,
      {},
      { exchanges: [], strategies: [], executions: [] },
      // Lists with rows in them, none of which reported a readable status.
      { strategies: [{ id: 'a' }, { id: 'b', status: null }, { id: 'c', status: '   ' }] },
      { executions: [{ id: 'a', status: 7 }, { id: 'b', status: true }] },
    ];

    for (const input of inputs) {
      const condition = deriveAlertCondition(input);
      expect(condition.evaluated, JSON.stringify(input ?? null)).toBe(false);
      expect(condition.firing).toBe(false);
      expect(condition.reported).toBe(0);
      expect(condition.summary).toBeNull();
    }
  });

  it('is evaluated as soon as ONE arm reads one status, even if the other two read none', () => {
    const condition = deriveAlertCondition({ strategies: [strategy('a', 'running')] });

    expect(condition.evaluated).toBe(true);
    expect(condition.firing).toBe(false);
    expect(condition.reported).toBe(1);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * TOTALITY — GARBAGE IN, AN ANSWER OUT
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('deriveAlertCondition — total over every input', () => {
  it('does not throw on a non-record input', () => {
    for (const input of [undefined, null, 0, 1, '', 'exchanges', true, false, [], [1, 2], NaN]) {
      expect(() => deriveAlertCondition(input)).not.toThrow();
      const condition = deriveAlertCondition(input);
      expect(condition.evaluated).toBe(false);
      expect(condition.firing).toBe(false);
      expect(condition.arms).toHaveLength(ALERT_ARMS.length);
    }
  });

  it('does not throw when a field set is not a list', () => {
    // A string is iterable and is still not a list: `"connected"` must not be read as eight
    // venues reporting one character each.
    const condition = deriveAlertCondition({
      exchanges: 'connected',
      strategies: 42,
      executions: { status: 'failed' },
    });

    expect(condition.evaluated).toBe(false);
    expect(condition.firing).toBe(false);
    for (const entry of condition.arms) expect(entry.armReported).toBe(0);
  });

  it('does not throw on items that are not records, and skips them', () => {
    const condition = deriveAlertCondition({
      exchanges: [null, undefined, 5, 'binance', [], ['status', 'x'], venue('bybit', 'disconnected')],
      strategies: [null, 0, false, strategy('a', 'crashed')],
      executions: [[], {}, { status: {} }, execution('ord', 'failed')],
    });

    expect(condition.firing).toBe(true);
    expect(condition.firedArms).toEqual([ARM_EXCHANGE, ARM_STRATEGY, ARM_EXECUTION]);
    // One readable status per arm; every unreadable item was skipped rather than counted.
    for (const entry of condition.arms) expect(entry.armReported).toBe(1);
  });

  it('does not throw on a status of the wrong type, and does not read it', () => {
    const condition = deriveAlertCondition({
      exchanges: [{ exchange_id: 'binance', status: 0 }],
      strategies: [{ id: 'a', status: ['error'] }],
      executions: [{ id: 'x', status: { value: 'failed' } }],
    });

    expect(condition.evaluated).toBe(false);
    expect(condition.firing).toBe(false);
  });

  it('leaves an unnamed firing item counted and unnamed', () => {
    const condition = deriveAlertCondition({ executions: [{ status: 'failed' }] });

    expect(arm(condition, ARM_EXECUTION).count).toBe(1);
    // No invented label. The count is the finding; the name is absent because it was absent.
    expect(arm(condition, ARM_EXECUTION).subjects).toEqual([]);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * `armDetail`
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('armDetail', () => {
  it('reads the subjects, then the server\'s own status spellings', () => {
    const condition = deriveAlertCondition({
      exchanges: [venue('binance', 'disconnected'), venue('bybit', 'disconnected')],
    });

    expect(armDetail(arm(condition, ARM_EXCHANGE))).toBe('binance, bybit — disconnected');
  });

  it('yields the states alone when no firing item named itself', () => {
    const condition = deriveAlertCondition({ executions: [{ status: 'rejected' }] });

    expect(armDetail(arm(condition, ARM_EXECUTION))).toBe('rejected');
  });

  it('is `null` for an arm that did not fire, and for anything that is not an arm', () => {
    const condition = deriveAlertCondition({ exchanges: [venue('binance')] });

    expect(armDetail(arm(condition, ARM_EXCHANGE))).toBeNull();
    for (const input of [undefined, null, 0, 'exchange', [], {}, { fired: 'yes' }]) {
      expect(armDetail(input)).toBeNull();
    }
  });
});
