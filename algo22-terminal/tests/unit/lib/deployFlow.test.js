/**
 * tests/unit/lib/deployFlow.test.js
 *
 * vyomquant-ui-redesign task 10.5 — the pure `Deploy_Confirmation_Flow` state machine.
 * design.md §8.3. Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 19.1.
 *
 * By-example coverage. The property tests for P13 (confirmation gating) and P14 (the
 * real-funds step on exactly the Live path) are tasks 10.6 and 10.7 and are not here.
 */

import { describe, expect, it } from 'vitest';

import {
  ACKNOWLEDGEMENT_LABEL,
  BLOCKER,
  DEPLOY_EVENT,
  DEPLOY_EVENTS,
  DEPLOY_PRESENTATION,
  DEPLOY_STATE,
  DEPLOY_STATES,
  DEPLOY_STEP,
  REJECTION,
  REVIEW_FIELD_IDS,
  acknowledgementOf,
  advanceRefusal,
  beginDeployFlow,
  buildDeployReview,
  canAdvance,
  canSubmit,
  currentStep,
  deployBlockers,
  deployPresentation,
  hasAckLiveStep,
  isDeployFlow,
  isLiveEnvironment,
  resolveDeployEnvironment,
  submissionOf,
  transition,
} from '../../../src/lib/deployFlow';

/* ── Fixtures ──────────────────────────────────────────────────────────────────────────── */

const ADDRESS = { strategyId: 'strat-7', version: '1.4' };

const account = {
  id: 'acct-91',
  label: 'Binance · …4821',
  exchange_id: 'binance',
};

const configFor = (environment, overrides = {}) => ({
  ...ADDRESS,
  environment,
  strategyName: 'RSI Reversion',
  exchange: 'binance',
  account,
  market: 'BTC/USDT',
  capital: '10000',
  tradeSizePct: '2',
  riskConfigId: 'risk-3',
  ...overrides,
});

/** Drive a flow to a state. Every route goes through `transition` — nothing is forged. */
const at = (state, environment = 'LIVE', overrides = {}) => {
  const configure = beginDeployFlow(configFor(environment, overrides));
  if (state === DEPLOY_STATE.CONFIGURE) return configure;
  if (state === DEPLOY_STATE.CANCELLED) return transition(configure, DEPLOY_EVENT.CANCEL);

  const review = transition(configure, DEPLOY_EVENT.ADVANCE);
  if (state === DEPLOY_STATE.REVIEW) return review;

  const ack = transition(review, DEPLOY_EVENT.ADVANCE);
  if (state === DEPLOY_STATE.ACK_LIVE) return ack;

  const submitting =
    ack.state === DEPLOY_STATE.SUBMITTING
      ? ack
      : transition(transition(ack, DEPLOY_EVENT.ACKNOWLEDGE), DEPLOY_EVENT.ADVANCE);
  if (state === DEPLOY_STATE.SUBMITTING) return submitting;

  if (state === DEPLOY_STATE.DEPLOYED) return transition(submitting, DEPLOY_EVENT.SUCCEEDED);
  if (state === DEPLOY_STATE.FAILED) return transition(submitting, DEPLOY_EVENT.REJECTED);
  throw new Error(`no route to ${state}`);
};

/* ── Environment resolution (total, and failing closed) ────────────────────────────────── */

describe('resolveDeployEnvironment', () => {
  it('resolves the three known targets whatever the casing or padding', () => {
    expect(resolveDeployEnvironment('LIVE')).toBe('LIVE');
    expect(resolveDeployEnvironment('live')).toBe('LIVE');
    expect(resolveDeployEnvironment('  LiVe ')).toBe('LIVE');
    expect(resolveDeployEnvironment('paper')).toBe('PAPER');
    expect(resolveDeployEnvironment('Backtest')).toBe('BACKTEST');
  });

  it('resolves null for null, undefined, an unknown string and a non-string', () => {
    expect(resolveDeployEnvironment(null)).toBeNull();
    expect(resolveDeployEnvironment(undefined)).toBeNull();
    expect(resolveDeployEnvironment('')).toBeNull();
    expect(resolveDeployEnvironment('production')).toBeNull();
    expect(resolveDeployEnvironment('paper trading')).toBeNull();
    expect(resolveDeployEnvironment(42)).toBeNull();
    expect(resolveDeployEnvironment({ id: 'LIVE' })).toBeNull();
    expect(resolveDeployEnvironment('constructor')).toBeNull();
  });

  it('treats only Live as live', () => {
    expect(isLiveEnvironment('live')).toBe(true);
    expect(isLiveEnvironment('PAPER')).toBe(false);
    expect(isLiveEnvironment(null)).toBe(false);
    expect(isLiveEnvironment('nonsense')).toBe(false);
  });

  it('fails closed on an unresolved target: no ack, no forward edge, only Cancel', () => {
    const flow = beginDeployFlow(configFor('production'));

    expect(flow.environment).toBeNull();
    expect(flow.steps.map((s) => s.id)).toEqual([DEPLOY_STEP.CONFIGURE]);
    expect(hasAckLiveStep(flow)).toBe(false);
    expect(acknowledgementOf(flow)).toBeNull();
    expect(canAdvance(flow)).toBe(false);
    expect(canSubmit(flow)).toBe(false);
    expect(flow.blockers.map((b) => b.code)).toContain(BLOCKER.ENVIRONMENT_UNCONFIRMED);

    // Not defaulted to Paper (which would submit with no acknowledgement) and not to Live.
    expect(transition(flow, DEPLOY_EVENT.ADVANCE).state).toBe(DEPLOY_STATE.CONFIGURE);
    expect(transition(flow, DEPLOY_EVENT.ADVANCE).rejection.reason).toBe(REJECTION.BLOCKED);
    expect(transition(flow, DEPLOY_EVENT.CANCEL).state).toBe(DEPLOY_STATE.CANCELLED);
  });
});

/* ── The step list is derived from the environment (Requirements 8.2, 8.4) ─────────────── */

describe('step list', () => {
  it('constructs the real-funds step only on the Live path', () => {
    const live = beginDeployFlow(configFor('LIVE'));

    expect(live.steps.map((s) => s.id)).toEqual([
      DEPLOY_STEP.CONFIGURE,
      DEPLOY_STEP.REVIEW,
      DEPLOY_STEP.ACK_LIVE,
    ]);
    expect(hasAckLiveStep(live)).toBe(true);
    expect(acknowledgementOf(live)).toEqual({
      statement: 'Confirming will place real orders on binance using real funds in account Binance · …4821.',
      label: ACKNOWLEDGEMENT_LABEL,
      control: 'checkbox',
    });
  });

  it('never constructs it for Paper or Backtest — the step is absent, not hidden', () => {
    for (const environment of ['PAPER', 'BACKTEST']) {
      const flow = beginDeployFlow(configFor(environment));

      expect(flow.steps.map((s) => s.id)).toEqual([
        DEPLOY_STEP.CONFIGURE,
        DEPLOY_STEP.REVIEW,
      ]);
      expect(hasAckLiveStep(flow)).toBe(false);
      expect(acknowledgementOf(flow)).toBeNull();
      // No step anywhere in the flow carries an acknowledgement object at all.
      expect(flow.steps.every((s) => s.acknowledgement === undefined)).toBe(true);
      // And no real-funds copy exists anywhere in the serialised flow.
      expect(JSON.stringify(flow)).not.toMatch(/real funds/i);
    }
  });

  it('keeps the acknowledgement statement honest when the venue and account are missing', () => {
    const flow = beginDeployFlow(
      configFor('LIVE', { exchange: null, account: null, accountLabel: null }),
    );

    expect(acknowledgementOf(flow).statement).toBe(
      'Confirming will place real orders using real funds.',
    );
  });

  it('names the current step, and reports none for the non-step states', () => {
    expect(currentStep(at(DEPLOY_STATE.CONFIGURE)).heading).toBe('Step 1 — Target');
    expect(currentStep(at(DEPLOY_STATE.REVIEW)).heading).toBe('Step 2 — Review');
    expect(currentStep(at(DEPLOY_STATE.ACK_LIVE)).heading).toBe('Step 3 — Real funds');
    expect(currentStep(at(DEPLOY_STATE.SUBMITTING))).toBeNull();
    expect(currentStep(at(DEPLOY_STATE.DEPLOYED))).toBeNull();
  });
});

/* ── Requirement 8.5 — title and confirm intent per environment ────────────────────────── */

describe('presentation', () => {
  it('differs by environment and is total over unknown targets', () => {
    expect(deployPresentation('live')).toBe(DEPLOY_PRESENTATION.LIVE);
    expect(deployPresentation('LIVE').title).toBe('Deploy to live trading');
    expect(deployPresentation('LIVE').confirmIntent).toBe('live');
    expect(deployPresentation('PAPER').title).toBe('Start paper session');
    expect(deployPresentation('PAPER').confirmIntent).toBe('neutral');
    expect(deployPresentation('BACKTEST').confirmIntent).toBe('neutral');
    expect(deployPresentation(null).environment).toBeNull();
    expect(deployPresentation('nonsense').confirmIntent).toBe('neutral');
  });
});

/* ── Legal and illegal transitions, state by state ─────────────────────────────────────── */

describe('transition table', () => {
  const LEGAL = {
    [DEPLOY_STATE.CONFIGURE]: [DEPLOY_EVENT.ADVANCE, DEPLOY_EVENT.CANCEL],
    [DEPLOY_STATE.REVIEW]: [DEPLOY_EVENT.ADVANCE, DEPLOY_EVENT.BACK, DEPLOY_EVENT.CANCEL],
    [DEPLOY_STATE.ACK_LIVE]: [
      DEPLOY_EVENT.ACKNOWLEDGE,
      DEPLOY_EVENT.WITHDRAW,
      DEPLOY_EVENT.BACK,
      DEPLOY_EVENT.CANCEL,
    ],
    [DEPLOY_STATE.SUBMITTING]: [DEPLOY_EVENT.SUCCEEDED, DEPLOY_EVENT.REJECTED],
    [DEPLOY_STATE.DEPLOYED]: [],
    [DEPLOY_STATE.FAILED]: [DEPLOY_EVENT.BACK, DEPLOY_EVENT.CANCEL],
    [DEPLOY_STATE.CANCELLED]: [],
  };

  it('is total: every state × every event answers with a flow and never throws', () => {
    for (const state of DEPLOY_STATES) {
      const flow = at(state);
      for (const event of DEPLOY_EVENTS) {
        const next = transition(flow, event);
        expect(isDeployFlow(next)).toBe(true);
        expect(DEPLOY_STATES).toContain(next.state);
      }
    }
  });

  it('leaves the state unchanged for every event the state has no edge for', () => {
    for (const state of DEPLOY_STATES) {
      const flow = at(state);
      const illegal = DEPLOY_EVENTS.filter((e) => !LEGAL[state].includes(e));
      for (const event of illegal) {
        const next = transition(flow, event);
        expect(next.state).toBe(state);
        expect(next.rejection).not.toBeNull();
        expect(next.rejection.event).toBe(event);
        expect(submissionOf(next)).toEqual(submissionOf(flow));
      }
    }
  });

  it('does not advance on an unrecognised event', () => {
    const review = at(DEPLOY_STATE.REVIEW, 'PAPER');

    for (const event of ['advance', 'ADVANCE ', 'SUBMIT', '', null, undefined, 7, {}]) {
      const next = transition(review, event);
      expect(next.state).toBe(DEPLOY_STATE.REVIEW);
      expect(next.rejection.reason).toBe(REJECTION.UNKNOWN_EVENT);
      expect(submissionOf(next)).toBeNull();
    }
  });

  it('refuses anything that is not a flow it produced, and submits nothing', () => {
    const forged = {
      state: DEPLOY_STATE.SUBMITTING,
      submission: { method: 'POST', path: '/api/anything' },
      steps: [],
    };

    expect(isDeployFlow(forged)).toBe(false);
    expect(submissionOf(forged)).toBeNull();
    expect(canSubmit(forged)).toBe(false);
    expect(acknowledgementOf(forged)).toBeNull();
    expect(hasAckLiveStep(forged)).toBe(false);

    const recovered = transition(forged, DEPLOY_EVENT.ADVANCE);
    expect(recovered.state).toBe(DEPLOY_STATE.CONFIGURE);
    expect(recovered.rejection.reason).toBe(REJECTION.UNRECOGNISED_FLOW);
    expect(submissionOf(recovered)).toBeNull();
  });

  it('walks §8.3 forwards and backwards on the Live path', () => {
    const configure = beginDeployFlow(configFor('LIVE'));
    const review = transition(configure, DEPLOY_EVENT.ADVANCE);
    expect(review.state).toBe(DEPLOY_STATE.REVIEW);
    expect(transition(review, DEPLOY_EVENT.BACK).state).toBe(DEPLOY_STATE.CONFIGURE);

    const ack = transition(review, DEPLOY_EVENT.ADVANCE);
    expect(ack.state).toBe(DEPLOY_STATE.ACK_LIVE);
    expect(transition(ack, DEPLOY_EVENT.BACK).state).toBe(DEPLOY_STATE.REVIEW);

    const ticked = transition(ack, DEPLOY_EVENT.ACKNOWLEDGE);
    expect(ticked.state).toBe(DEPLOY_STATE.ACK_LIVE);
    expect(ticked.acknowledged).toBe(true);
    expect(transition(ticked, DEPLOY_EVENT.WITHDRAW).acknowledged).toBe(false);

    const submitting = transition(ticked, DEPLOY_EVENT.ADVANCE);
    expect(submitting.state).toBe(DEPLOY_STATE.SUBMITTING);
    expect(transition(submitting, DEPLOY_EVENT.SUCCEEDED).state).toBe(DEPLOY_STATE.DEPLOYED);

    const failed = transition(submitting, DEPLOY_EVENT.REJECTED);
    expect(failed.state).toBe(DEPLOY_STATE.FAILED);
    expect(transition(failed, DEPLOY_EVENT.BACK).state).toBe(DEPLOY_STATE.REVIEW);
  });

  it('takes the direct Review → Submitting edge for Paper and Backtest (Requirement 8.4)', () => {
    for (const environment of ['PAPER', 'BACKTEST']) {
      const review = at(DEPLOY_STATE.REVIEW, environment);
      const next = transition(review, DEPLOY_EVENT.ADVANCE);

      expect(next.state).toBe(DEPLOY_STATE.SUBMITTING);
      expect(next.acknowledged).toBe(false);
      expect(acknowledgementOf(next)).toBeNull();
    }
  });

  it('retracts an acknowledgement carried out of AckLive by Back or by a failure', () => {
    const ticked = transition(at(DEPLOY_STATE.ACK_LIVE), DEPLOY_EVENT.ACKNOWLEDGE);

    const back = transition(ticked, DEPLOY_EVENT.BACK);
    expect(back.state).toBe(DEPLOY_STATE.REVIEW);
    expect(back.acknowledged).toBe(false);
    // Returning to AckLive requires ticking again before the confirm reopens.
    const again = transition(back, DEPLOY_EVENT.ADVANCE);
    expect(again.state).toBe(DEPLOY_STATE.ACK_LIVE);
    expect(canSubmit(again)).toBe(false);

    const failed = transition(
      transition(ticked, DEPLOY_EVENT.ADVANCE),
      DEPLOY_EVENT.REJECTED,
    );
    expect(failed.acknowledged).toBe(false);
  });

  it('cannot pre-satisfy the acknowledgement before reaching the step that states it', () => {
    for (const state of [DEPLOY_STATE.CONFIGURE, DEPLOY_STATE.REVIEW]) {
      const early = transition(at(state), DEPLOY_EVENT.ACKNOWLEDGE);

      expect(early.acknowledged).toBe(false);
      expect(early.rejection.reason).toBe(REJECTION.NOT_LEGAL_IN_STATE);
    }
  });
});

/* ── The submit guard (Requirement 8.3, P13's predicate) ───────────────────────────────── */

describe('canSubmit', () => {
  it('is false at Configure for every environment', () => {
    for (const environment of ['LIVE', 'PAPER', 'BACKTEST', null, 'nonsense']) {
      const flow = at(DEPLOY_STATE.CONFIGURE, environment);
      expect(canSubmit(flow)).toBe(false);
      expect(submissionOf(flow)).toBeNull();
    }
  });

  it('is false at Review on the Live path — the next edge is AckLive, not the backend', () => {
    const review = at(DEPLOY_STATE.REVIEW, 'LIVE');

    expect(canAdvance(review)).toBe(true);
    expect(canSubmit(review)).toBe(false);
    expect(submissionOf(review)).toBeNull();
  });

  it('is true at Review for Paper and Backtest', () => {
    expect(canSubmit(at(DEPLOY_STATE.REVIEW, 'PAPER'))).toBe(true);
    expect(canSubmit(at(DEPLOY_STATE.REVIEW, 'BACKTEST'))).toBe(true);
  });

  it('is false at an un-acknowledged AckLive and true only after the explicit action', () => {
    const ack = at(DEPLOY_STATE.ACK_LIVE);

    expect(canSubmit(ack)).toBe(false);
    expect(transition(ack, DEPLOY_EVENT.ADVANCE).state).toBe(DEPLOY_STATE.ACK_LIVE);
    expect(transition(ack, DEPLOY_EVENT.ADVANCE).rejection.reason).toBe(
      REJECTION.ACKNOWLEDGEMENT_REQUIRED,
    );
    expect(advanceRefusal(ack)).toContain(ACKNOWLEDGEMENT_LABEL);

    const ticked = transition(ack, DEPLOY_EVENT.ACKNOWLEDGE);
    expect(canSubmit(ticked)).toBe(true);
    expect(submissionOf(ticked)).toBeNull();

    expect(canSubmit(transition(ticked, DEPLOY_EVENT.WITHDRAW))).toBe(false);
  });

  it('is false in every state that has no ADVANCE edge', () => {
    for (const state of [
      DEPLOY_STATE.SUBMITTING,
      DEPLOY_STATE.DEPLOYED,
      DEPLOY_STATE.FAILED,
      DEPLOY_STATE.CANCELLED,
    ]) {
      expect(canSubmit(at(state))).toBe(false);
    }
  });

  it('carries the submission in Submitting only, and counts one entry per confirm', () => {
    const ticked = transition(at(DEPLOY_STATE.ACK_LIVE), DEPLOY_EVENT.ACKNOWLEDGE);
    const submitting = transition(ticked, DEPLOY_EVENT.ADVANCE);

    expect(submitting.submissionCount).toBe(1);
    expect(submissionOf(submitting)).not.toBeNull();

    // Re-confirming in flight adds nothing: Submitting has no ADVANCE edge.
    const again = transition(submitting, DEPLOY_EVENT.ADVANCE);
    expect(again.state).toBe(DEPLOY_STATE.SUBMITTING);
    expect(again.submissionCount).toBe(1);

    // And the outcome states carry no request to re-send.
    expect(submissionOf(transition(submitting, DEPLOY_EVENT.SUCCEEDED))).toBeNull();
    expect(submissionOf(transition(submitting, DEPLOY_EVENT.REJECTED))).toBeNull();
  });

  it('issues the route and payload pages/Strategies.jsx already issues', () => {
    const submission = submissionOf(at(DEPLOY_STATE.SUBMITTING, 'LIVE'));

    expect(submission.method).toBe('POST');
    expect(submission.path).toBe(
      '/api/strategy-operations/strategies/strat-7/versions/1.4/deploy',
    );
    expect(submission.body).toEqual({
      mode: 'live',
      exchange_account_id: 'acct-91',
      risk_config_id: 'risk-3',
      execution_config: { max_order_notional: 200 },
    });
    expect(submission.query).toEqual({ environment: 'live' });
  });
});

/* ── Requirement 8.1 — the eight review fields ─────────────────────────────────────────── */

describe('review grid', () => {
  it('declares the eight fields in a stable order', () => {
    expect(REVIEW_FIELD_IDS).toEqual([
      'strategyName',
      'version',
      'exchange',
      'account',
      'market',
      'sizing',
      'riskConfig',
      'estimatedExposure',
    ]);
  });

  it('renders all eight for every configuration, including none at all', () => {
    for (const config of [undefined, null, {}, configFor('LIVE')]) {
      const review = buildDeployReview(config);
      expect(review.map((row) => row.id)).toEqual(REVIEW_FIELD_IDS);
    }
  });

  it('reads every supplied field', () => {
    const byId = Object.fromEntries(
      buildDeployReview(configFor('LIVE')).map((row) => [row.id, row.reported]),
    );

    expect(byId.strategyName).toEqual({ available: true, value: 'RSI Reversion' });
    expect(byId.version).toEqual({ available: true, value: '1.4' });
    expect(byId.exchange).toEqual({ available: true, value: 'binance' });
    expect(byId.account).toEqual({ available: true, value: 'Binance · …4821' });
    expect(byId.market).toEqual({ available: true, value: 'BTC/USDT' });
    expect(byId.sizing).toEqual({ available: true, value: '10000 allocated · 2% per trade' });
    expect(byId.riskConfig).toEqual({ available: true, value: 'risk-3' });
    // Read from deployPreflight's own `max_order_notional`, not recomputed here.
    expect(byId.estimatedExposure).toEqual({ available: true, value: 200 });
  });

  it('reports a field the configuration does not carry as not available, with a reason', () => {
    const review = buildDeployReview(
      configFor('PAPER', { market: null, riskConfigId: null, capital: '' }),
    );
    const byId = Object.fromEntries(review.map((row) => [row.id, row.reported]));

    expect(byId.market.available).toBe(false);
    expect(byId.market.reason).toBe('No market is selected.');
    expect(byId.riskConfig.available).toBe(false);
    expect(byId.riskConfig.reason).toMatch(/risk configuration/i);
    // Not defaulted: no zero, no empty string, no fabricated value.
    expect(byId.market.value).toBeUndefined();
    expect(byId.estimatedExposure.available).toBe(false);
    // The half that *is* configured still reads.
    expect(byId.sizing).toEqual({ available: true, value: '2% per trade' });
  });

  it('does not block the flow on a missing field', () => {
    const flow = beginDeployFlow(
      configFor('PAPER', {
        market: null,
        riskConfigId: null,
        capital: null,
        tradeSizePct: null,
        strategyName: null,
        exchange: null,
        account: null,
      }),
    );

    expect(flow.review.filter((row) => row.reported.available === false).length)
      .toBeGreaterThan(0);
    expect(flow.blockers).toEqual([]);
    expect(canAdvance(flow)).toBe(true);
    expect(advanceRefusal(flow)).toBeNull();

    const submitting = transition(
      transition(flow, DEPLOY_EVENT.ADVANCE),
      DEPLOY_EVENT.ADVANCE,
    );
    expect(submitting.state).toBe(DEPLOY_STATE.SUBMITTING);
    expect(submissionOf(submitting).body).toEqual({ mode: 'paper' });
  });

  it('keeps a reported zero rather than calling it absent', () => {
    const byId = Object.fromEntries(
      buildDeployReview(configFor('PAPER', { sizingLabel: '0 allocated' })).map((row) => [
        row.id,
        row.reported,
      ]),
    );

    expect(byId.sizing).toEqual({ available: true, value: '0 allocated' });
  });
});

/* ── Blockers are structural, not gate conditions (Requirement 19.1) ───────────────────── */

describe('deployBlockers', () => {
  it('names a missing strategy and a missing version, in Strategies.jsx\'s own words', () => {
    const flow = beginDeployFlow({ environment: 'LIVE' });
    const codes = flow.blockers.map((b) => b.code);

    expect(codes).toEqual([BLOCKER.NO_STRATEGY, BLOCKER.NO_VERSION]);
    expect(canAdvance(flow)).toBe(false);
    expect(advanceRefusal(flow)).toMatch(/no current version to deploy/i);
  });

  it('is empty for a fully addressed deployment', () => {
    expect(deployBlockers(beginDeployFlow(configFor('LIVE')))).toEqual([]);
    expect(deployBlockers(configFor('PAPER'))).toEqual([]);
  });

  it('carries no preflight condition — deployability stays deployPreflight\'s', () => {
    const codes = Object.values(BLOCKER);

    expect(codes).toEqual(['ENVIRONMENT_UNCONFIRMED', 'NO_STRATEGY', 'NO_VERSION']);
  });
});

/* ── Purity ────────────────────────────────────────────────────────────────────────────── */

describe('purity', () => {
  it('freezes every flow and never mutates the one it was given', () => {
    const review = at(DEPLOY_STATE.REVIEW, 'PAPER');
    const snapshot = JSON.stringify(review);

    transition(review, DEPLOY_EVENT.ADVANCE);
    transition(review, DEPLOY_EVENT.CANCEL);

    expect(Object.isFrozen(review)).toBe(true);
    expect(JSON.stringify(review)).toBe(snapshot);
  });

  it('is deterministic: the same configuration and events give the same flow', () => {
    const once = at(DEPLOY_STATE.SUBMITTING, 'LIVE');
    const twice = at(DEPLOY_STATE.SUBMITTING, 'LIVE');

    expect(JSON.stringify(once)).toBe(JSON.stringify(twice));
  });
});
