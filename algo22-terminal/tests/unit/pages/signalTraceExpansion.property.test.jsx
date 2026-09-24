/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Feature: vyomquant-ui-redesign — Property 17
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Task 21.5. `design.md` §10.3, Requirement 9.3.
 *
 *   * **Property 17: Every trace stage starts collapsed and expands independently.**
 *     **Validates: Requirements 9.3**
 *
 * `tests/unit/lib/signalTraceStages.property.test.js` (Properties 15 and 16) holds the
 * projection: nine rows, in order, each in a state that is a function of its own backing
 * record. This file holds the other half of §10.3 — what happens when a user starts
 * *activating* those rows — and it is asserted against the page's real markup rather than
 * against a model of it, because the claim Requirement 9.3 makes is a claim about the DOM.
 *
 * WHY THIS QUERIES BY ROLE AND `aria-expanded`, AND NEVER BY CLASS
 * ---------------------------------------------------------------
 * A disclosure is not "a row that looks open". It is a `<button aria-expanded>` naming the
 * region it controls, and a screen-reader user reads *only* that wiring — the chevron, the
 * marker glyph and the rail are all `aria-hidden` in `StageRow`, deliberately. So every
 * assertion below goes through the same three facts an assistive technology would read:
 *
 *   1. the control is a `button`, found by role, whose accessible name announces the stage's
 *      number and name (so a row that lost its number, or that let a `<div onClick>` back in,
 *      fails here rather than passing quietly),
 *   2. `aria-expanded` is the state — not a colour, not a class, not a chevron direction,
 *   3. `aria-controls` resolves to a real `role="region"` element that points back at this
 *      button with `aria-labelledby`, and no two buttons name the same region.
 *
 * That is what makes this test fail on a regression in the wiring rather than on a rename.
 * A refactor that swapped `flex` for `grid` changes nothing here; one that dropped
 * `aria-controls`, collapsed the nine regions into one shared panel, or replaced the record
 * of expanded ids with a single "open row" fails on the first run.
 *
 * WHAT IS REAL HERE
 * -----------------
 * All of it. `SignalTimeline` and `StageRow` from `pages/SignalTrace.jsx`, the real
 * `buildSignalTraceStages` projection feeding them, the real `ds/*` components inside the
 * expanded bodies, and real click events. There is no double and no mock in this file: the
 * component under test needs no network, no clock and no socket, so introducing one would
 * only give the property something to be true about other than the page.
 *
 * WHY THE PROPERTY IS STATED AS A PARITY
 * -------------------------------------
 * "Expands independently" is easy to state weakly — expand two rows, check both are open —
 * and a weak statement passes against the exact implementation Requirement 9.3 forbids as
 * soon as the second row is expanded before the first is closed. The honest statement is
 * over an arbitrary *sequence* of activations: after each one, every stage's `aria-expanded`
 * equals the parity of the activations on **that stage alone**. That single equation carries
 * both halves of the requirement — a stage responds to its own activations (so it toggles),
 * and nothing else (so no other row's activation can open or close it) — and it is checked
 * after *every* activation, so a cross-talk bug is caught at the click that causes it rather
 * than being cancelled out by a later toggle.
 *
 * The third test states the independence half a second way, differentially: two runs whose
 * activations agree on one stage and differ arbitrarily everywhere else leave that stage in
 * the same place. That is redundant with the parity equation by construction, and it is here
 * anyway because it is the form the requirement is written in, and because it would survive a
 * mistake in the parity bookkeeping above.
 */

import { describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { computeAccessibleName } from 'dom-accessibility-api';
import fc from 'fast-check';

import { SignalTimeline } from '../../../src/pages/SignalTrace';
import { buildSignalTraceStages } from '../../../src/lib/signalTraceStages';

/** design.md: "minimum 100 iterations per property"; this repo's convention is 300. */
const RUNS = { numRuns: 300 };

/**
 * Per-test timeout, over the config's 15s default.
 *
 * Not a hang and not masking one. Every run here mounts nine expanded stage bodies —
 * several hundred elements — and clicks through them, and jsdom charges real time for that:
 * measured on this machine a run costs roughly 100ms to 300ms, so 300 of them is a minute or
 * two per property. The 15s default is sized for the render-once tests that make up the rest
 * of this suite. Lowering `numRuns` instead would buy the same wall clock by asserting less.
 */
const SLOW = 240_000;

/**
 * The nine rows a user can activate, spelled out rather than read from the module.
 *
 * Same reason Properties 15 and 16 spell out `CANONICAL_IDS`: a test that read its
 * expectations out of the code under test would agree with any renaming, including a wrong
 * one. `number` and `name` are what the accessible name has to announce, so they are the
 * handle every query below uses.
 */
const STAGE_ROWS = Object.freeze([
  Object.freeze({ id: 'market-data', number: 1, name: 'Market data' }),
  Object.freeze({ id: 'indicators', number: 2, name: 'Indicators' }),
  Object.freeze({ id: 'model', number: 3, name: 'Model' }),
  Object.freeze({ id: 'logic', number: 4, name: 'Logic' }),
  Object.freeze({ id: 'signal', number: 5, name: 'Signal' }),
  Object.freeze({ id: 'order-decision', number: 6, name: 'Order decision' }),
  Object.freeze({ id: 'submission', number: 7, name: 'Submission' }),
  Object.freeze({ id: 'execution', number: 8, name: 'Execution' }),
  Object.freeze({ id: 'position-update', number: 9, name: 'Position update' }),
]);

const STAGE_IDS = STAGE_ROWS.map((row) => row.id);

/**
 * The pattern the stage's own control must match, anchored at the start of the name.
 *
 * The chevron and the state glyph are `aria-hidden`, so the first thing announced is the
 * stage number and then its name. Anchoring matters: `stage.summary ?? stage.reason` also
 * lands in the button, and §10.2's lifecycle-ended reason names another stage ("stopped at
 * Order decision"), so an unanchored match would let stage 7's control answer to stage 6.
 */
const controlPattern = ({ number, name }) => new RegExp(`^${number}\\s+${name}\\b`);

/* ══════════════════════════════════════════════════════════════════════════
 * READING THE TIMELINE THE WAY AN ASSISTIVE TECHNOLOGY WOULD
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The nine disclosures, paired with the regions they control.
 *
 * Everything this returns was found by role and by ARIA relationship: a `button` element
 * carrying `aria-expanded` and `aria-controls`, an accessible name computed the way an
 * assistive technology computes it, and the controlled region resolved through
 * `getElementById` — literally what a screen reader does with `aria-controls`, and why a
 * region rendered conditionally instead of hidden is caught here rather than passing:
 * `aria-controls` would name an element that does not exist.
 *
 * WHY `computeAccessibleName` AND NOT `getByRole(…, {name})`
 * --------------------------------------------------------
 * They are the same computation — `dom-accessibility-api` is what Testing Library's own
 * `name` option calls. What differs is the price: `getAllByRole` first filters the whole
 * tree by accessibility, which in jsdom means a `getComputedStyle` walk per candidate, and
 * these nine expanded bodies are several hundred elements. Measured on this machine it is
 * the difference between roughly 1.8s and 20ms per render, i.e. between a property that runs
 * 300 times and one that cannot. `renders the nine controls into the accessibility tree`
 * below keeps the Testing Library role queries themselves in the suite, over the five
 * families as examples, so nothing about the exposed roles goes unasserted.
 *
 * WHY THE NAME CHECK IS COMPUTED ONCE PER DISTINCT ROW SET
 * ------------------------------------------------------
 * Even direct, `computeAccessibleName` is the expensive line here — it consults
 * `getComputedStyle` per descendant, and jsdom's is slow. It is also, for a given row set,
 * answering the same question every time: the nine names are a function of the nine stages'
 * ids, numbers, states and summary text, so two runs whose rows agree on those cannot
 * disagree on their names. `nameKey` below is exactly that tuple, and the check runs on each
 * distinct one — which over 300 runs is the ten row sets the generator can produce, not 300
 * repetitions of the same ten answers. The cheap structural half (nine controls, each naming
 * its own existing region, no two sharing one) runs on every single render.
 */
const NAMES_VERIFIED = new Set();

/**
 * What the nine accessible names are a function of.
 *
 * Deliberately the fields `StageRow` puts in the button and nothing else — number, name,
 * state (which chooses the state word) and the summary line, which is
 * `stage.summary ?? stage.reason ?? word`. A change to any of them is a new key and gets
 * checked again.
 */
const nameKey = (stages) =>
  JSON.stringify(
    stages.map((stage) => [stage.number, stage.name, stage.state, stage.summary, stage.reason]),
  );

function readDisclosures(container, stages) {
  // Role and ARIA state, in the selector: a `<div onClick>` with a chevron does not match
  // this, and neither does a button that stopped saying what it controls.
  const buttons = [...container.querySelectorAll('button[aria-expanded][aria-controls]')];

  // Nine controls, no more: the page must not have grown a tenth disclosure or a duplicate.
  expect(buttons).toHaveLength(STAGE_ROWS.length);

  const key = nameKey(stages);
  const checkNames = !NAMES_VERIFIED.has(key);
  NAMES_VERIFIED.add(key);

  const controlled = new Set();
  const labels = new Set();

  const disclosures = STAGE_ROWS.map((row, index) => {
    const button = buttons[index];

    // The name an assistive technology announces, asserted to BE this row's — so the pairing
    // fails both on a control that lost its stage number and on a reordering of the nine.
    if (checkNames) {
      expect(
        computeAccessibleName(button),
        `stage ${row.number} (${row.name}) is not the ${index + 1}th control`,
      ).toMatch(controlPattern(row));
    }

    const regionId = button.getAttribute('aria-controls');
    expect(regionId, `stage ${row.number} controls nothing`).toBeTruthy();

    const region = document.getElementById(regionId);
    expect(region, `stage ${row.number}'s aria-controls names no element`).not.toBeNull();
    expect(region.getAttribute('role')).toBe('region');
    // The relationship is asserted in BOTH directions: the region points back at the control
    // that names it, which is what gives the region an accessible name at all.
    expect(button.id).toBeTruthy();
    expect(region.getAttribute('aria-labelledby')).toBe(button.id);

    controlled.add(regionId);
    labels.add(button.id);

    return { ...row, button, region };
  });

  // Independence is structural before it is behavioural: nine controls, nine distinct
  // regions. One shared region named by every button would satisfy every parity check below
  // and still collapse the nine rows into one.
  expect(controlled.size).toBe(STAGE_ROWS.length);
  expect(labels.size).toBe(STAGE_ROWS.length);

  return disclosures;
}

/** A stage's state as the ARIA attribute reports it, and nothing else. */
const isExpanded = ({ button }) => button.getAttribute('aria-expanded') === 'true';

/**
 * One stage's state, checked on both channels that carry it.
 *
 * `aria-expanded` is what is announced; `hidden` is what decides whether the region is in
 * the accessibility tree at all. A row whose attribute says "expanded" over a region still
 * hidden announces a body the user cannot reach, so the two are asserted together rather
 * than trusting either alone.
 */
function expectState(disclosure, expected) {
  const where = `stage ${disclosure.number} (${disclosure.name})`;
  expect(isExpanded(disclosure), `${where} aria-expanded`).toBe(expected);
  expect(disclosure.region.hasAttribute('hidden'), `${where} region hidden`).toBe(!expected);
}

/** Activate a row exactly as a user does — a click on its own control. */
const activate = (disclosure) => fireEvent.click(disclosure.button);

/* ══════════════════════════════════════════════════════════════════════════
 * GENERATORS
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The payloads behind the rows.
 *
 * Property 17 is about expansion, not about stage semantics, so this generator is
 * deliberately smaller than Properties 15 and 16's — it does not need to reach every
 * malformed shape, it needs to reach every *rendering* a row can have, because that is what
 * the expansion has to survive. The five families below are what the design allows a row to
 * be, and the witness counters at the end of the first test assert all five states were
 * actually rendered:
 *
 *   * a full trace — every stage `complete`, every expanded body carrying real detail,
 *   * a trace blocked at risk validation — §10.2's pass two, stages 6–9 `not-available` with
 *     a reason that names another stage (see {@link controlPattern}),
 *   * an in-flight signal — later stages `pending`,
 *   * `ml_inference.applicable: false` — the one route to `not-applicable`,
 *   * an empty or absent body — nine rows of markers, which is the case where a row's
 *     summary line is at its shortest and its accessible name is nothing but the number,
 *     the name and the not-available text.
 *
 * `degraded` is generated independently of all five, because it renders an `Alert` ABOVE the
 * rows: it changes what else is in the tree, and a query that picked up the alert as a tenth
 * control or a tenth region would fail in {@link readDisclosures}.
 */
const KNOWN_EVENTS = [
  'SIGNAL_GENERATED',
  'RISK_EVALUATED',
  'ORDER_CREATED',
  'EXCHANGE_RESPONSE',
  'EXECUTED',
  'POSITION_UPDATED',
];

const event = (name, data = {}) => ({
  event: name,
  timestamp: '2024-05-01T12:04:00Z',
  data,
});

const dagSection = () => ({
  source: 'signal_trace_engine',
  nodes: [
    {
      node_id: 'rsi_14',
      node_type: 'INDICATOR',
      node_label: 'RSI 14',
      status: 'pass',
      execution_ms: 11,
      reading: { rsi_14: 28.4 },
      inputs: [{ port: 'candles', value: { closes: 3 } }],
      outputs: [{ port: 'value', value: 28.4 }],
    },
    {
      node_id: 'entry_gate',
      node_type: 'LOGIC',
      node_label: 'RSI < 30 AND close > EMA50',
      status: 'pass',
      execution_ms: 1,
      reading: { result: true },
    },
  ],
});

const signalSection = () => ({
  decision: 'BUY',
  market_info: { symbol: 'BTC/USDT', timeframe: '15m', reported: { close: 63118 } },
  indicators: { rsi_14: 28.4, ema_50: 63120 },
});

const FULL_PAYLOAD = Object.freeze({
  kind: 'complete',
  payload: {
    signal: signalSection(),
    trace: {
      dag_nodes: dagSection(),
      ml_inference: {
        source: 'signal_trace_engine',
        applicable: true,
        detail: { model_id: 'm-1', confidence: 0.68, inference_ms: 4 },
      },
      risk_validation: {
        source: 'signal_trace_engine',
        detail: { passed: true, blocked: false, position_size: 0.014, checks: [{ name: 'exposure', passed: true }] },
      },
      execution: {
        source: 'signal_trace_engine',
        outcome: { execution_price: 63118, filled_quantity: 0.014, latency_ms: 12, executed_at: '2024-05-01T12:04:03Z' },
        exchange_response: { status: 'FILLED', exchange_order_id: 'x-9F2' },
      },
    },
    timeline: KNOWN_EVENTS.map((name) => event(name, { decision: 'BUY', symbol: 'BTC/USDT' })),
    lifecycle_state_source: 'canonical',
  },
});

const BLOCKED_PAYLOAD = Object.freeze({
  kind: 'blocked',
  payload: {
    signal: signalSection(),
    trace: {
      dag_nodes: dagSection(),
      ml_inference: { source: 'signal_trace_engine', applicable: true, detail: { model_id: 'm-1', confidence: 0.31 } },
      risk_validation: {
        source: 'signal_trace_engine',
        detail: { passed: false, blocked: true, reason: 'Daily loss limit reached' },
      },
    },
    timeline: [
      event('SIGNAL_GENERATED', { decision: 'BUY' }),
      event('RISK_EVALUATED', { risk_passed: false, risk_reason: 'Daily loss limit reached' }),
    ],
  },
});

const PENDING_PAYLOAD = Object.freeze({
  kind: 'pending',
  payload: {
    signal: signalSection(),
    trace: { dag_nodes: dagSection() },
    timeline: [event('SIGNAL_GENERATED', { decision: 'BUY' })],
  },
});

const NOT_APPLICABLE_PAYLOAD = Object.freeze({
  kind: 'not-applicable',
  payload: {
    signal: signalSection(),
    // Empty nodes is §10.2's retention route for stage 4, so this payload carries
    // `not-applicable` AND `not-available` at once.
    trace: {
      dag_nodes: { source: 'signals_row', nodes: [] },
      ml_inference: { source: 'signal_trace_engine', applicable: false, detail: null },
    },
    timeline: [event('SIGNAL_GENERATED', { decision: 'HOLD' })],
  },
});

/** No body at all — the row set the page still has to render nine of. */
const anyEmptyPayload = fc
  .constantFrom(null, undefined, {}, { signal: null, trace: null, timeline: [] })
  .map((payload) => ({ kind: 'empty', payload }));

const anyTracePayload = fc.oneof(
  { weight: 4, arbitrary: fc.constant(FULL_PAYLOAD) },
  { weight: 3, arbitrary: fc.constant(BLOCKED_PAYLOAD) },
  { weight: 3, arbitrary: fc.constant(PENDING_PAYLOAD) },
  { weight: 3, arbitrary: fc.constant(NOT_APPLICABLE_PAYLOAD) },
  { weight: 2, arbitrary: anyEmptyPayload },
);

const anyDegraded = fc.oneof(
  { weight: 3, arbitrary: fc.constant(null) },
  {
    weight: 1,
    arbitrary: fc.constant({
      migration: '005b_signal_lifecycle_and_idempotency.sql',
      reason: 'public.signals does not carry order_lifecycle_state.',
      unrepresentable_lifecycle_states: ['GENERATED'],
    }),
  },
);

/** A rendered timeline: the projection's own output, exactly as the page passes it over. */
const anyTimeline = fc
  .tuple(anyTracePayload, anyDegraded)
  .map(([{ kind, payload }, degraded]) => {
    const body =
      degraded === null || payload === null || typeof payload !== 'object'
        ? payload
        : { ...payload, degraded };
    return { kind, degraded: degraded !== null, trace: buildSignalTraceStages(body) };
  });

/** An activation sequence: which rows the user clicks, in order, repeats and all. */
const anyActivations = fc.array(fc.constantFrom(...STAGE_IDS), { maxLength: 12 });

/** Render one timeline the way `SignalTrace` renders it, and hand back the nine rows. */
function mount({ trace }) {
  const { container } = render(
    <SignalTimeline stages={trace.stages} degraded={trace.degraded} />,
  );
  return readDisclosures(container, trace.stages);
}

/* ══════════════════════════════════════════════════════════════════════════
 * Property 17 — collapsed on first render, and independent thereafter
 * ══════════════════════════════════════════════════════════════════════════ */

describe('Property 17: every trace stage starts collapsed and expands independently', () => {
  it('renders all nine stages collapsed, each controlling its own region', () => {
    const kinds = new Set();
    const states = new Set();
    let degradedRuns = 0;

    fc.assert(
      fc.property(anyTimeline, (variant) => {
        try {
          const disclosures = mount(variant);

          disclosures.forEach((disclosure) => expectState(disclosure, false));

          kinds.add(variant.kind);
          variant.trace.stages.forEach((stage) => states.add(stage.state));
          if (variant.degraded) degradedRuns += 1;
        } finally {
          cleanup();
        }
      }),
      RUNS,
    );

    // Non-vacuity: every row family and all five §10.2 states were actually rendered, and
    // the degraded alert was in the tree on some of the runs.
    expect([...kinds].sort()).toEqual(['blocked', 'complete', 'empty', 'not-applicable', 'pending']);
    expect([...states].sort()).toEqual([
      'blocked',
      'complete',
      'not-applicable',
      'not-available',
      'pending',
    ]);
    expect(degradedRuns, 'no run rendered the degraded note').toBeGreaterThan(0);
  }, SLOW);

  it('leaves each stage expanded exactly when its own activations are odd, after every one', () => {
    let longestRun = 0;
    let repeatedActivation = 0;

    fc.assert(
      fc.property(anyTimeline, anyActivations, (variant, activations) => {
        try {
          const disclosures = mount(variant);
          const byId = new Map(disclosures.map((disclosure) => [disclosure.id, disclosure]));
          const counts = new Map(STAGE_IDS.map((id) => [id, 0]));

          // First render, before any activation: Requirement 9.3's own clause.
          disclosures.forEach((disclosure) => expectState(disclosure, false));

          activations.forEach((id) => {
            activate(byId.get(id));
            counts.set(id, counts.get(id) + 1);

            // The whole property, after EVERY activation: each row is open iff its own
            // activations are odd. Asserting all nine each time is what catches cross-talk
            // at the click that causes it — a later toggle cannot cancel it out of view.
            disclosures.forEach((disclosure) => {
              expectState(disclosure, counts.get(disclosure.id) % 2 === 1);
            });
          });

          longestRun = Math.max(longestRun, activations.length);
          if (new Set(activations).size < activations.length) repeatedActivation += 1;
        } finally {
          cleanup();
        }
      }),
      RUNS,
    );

    // Non-vacuity: the sequences were long enough to interleave, and some row was activated
    // more than once — without a repeat there is no collapse in the sample at all.
    expect(longestRun, 'no run activated more than a few rows').toBeGreaterThan(6);
    expect(repeatedActivation, 'no run activated the same row twice').toBeGreaterThan(0);
  }, SLOW);

  it('leaves a stage where it was however the other eight are activated', () => {
    fc.assert(
      fc.property(
        anyTimeline,
        fc.constantFrom(...STAGE_IDS),
        fc.array(fc.constantFrom(...STAGE_IDS), { maxLength: 8 }),
        fc.array(fc.constantFrom(...STAGE_IDS), { maxLength: 8 }),
        (variant, target, first, second) => {
          // The two sequences agree on the target's OWN activations and differ arbitrarily
          // everywhere else — the requirement's "unaffected by activations on any other
          // stage", stated as a differential rather than as a parity.
          const own = first.filter((id) => id === target);
          const interference = second.filter((id) => id !== target);
          const sequences = [first, [...interference, ...own]];

          const outcomes = sequences.map((sequence) => {
            try {
              const disclosures = mount(variant);
              const byId = new Map(disclosures.map((disclosure) => [disclosure.id, disclosure]));
              sequence.forEach((id) => activate(byId.get(id)));
              return isExpanded(byId.get(target));
            } finally {
              cleanup();
            }
          });

          expect(outcomes[0]).toBe(own.length % 2 === 1);
          expect(outcomes[1]).toBe(outcomes[0]);
        },
      ),
      RUNS,
    );
  }, SLOW);

  /*
    The claims above read ARIA attributes off the DOM, which is fast enough to state a
    property 300 times over. This one states what the ACCESSIBILITY TREE holds, through
    Testing Library's own role queries, over the five families as examples — the two together
    are what "collapsed" means: `aria-expanded="false"` announced on a control that is exposed
    as a button, and a region that is not in the tree at all until it is opened.
  */
  it('renders the nine controls into the accessibility tree, and no region until one opens', () => {
    [FULL_PAYLOAD, BLOCKED_PAYLOAD, PENDING_PAYLOAD, NOT_APPLICABLE_PAYLOAD, { payload: {} }].forEach(
      ({ payload }) => {
        const trace = buildSignalTraceStages(payload);
        render(<SignalTimeline stages={trace.stages} degraded={trace.degraded} />);

        // Each row is exposed as a button, named by its number and its stage name, and each
        // announces itself collapsed. Found by role, as a screen-reader user reaches it.
        STAGE_ROWS.forEach((row) => {
          const control = screen.getByRole('button', { name: controlPattern(row) });
          expect(control.getAttribute('aria-expanded')).toBe('false');
        });

        // Nine collapsed rows, so there is nothing in the tree to read: a collapsed region is
        // not merely invisible, it is absent, which is what the `hidden` attribute buys and a
        // CSS class would not.
        expect(screen.queryAllByRole('region')).toHaveLength(0);

        // Open the middle row, and exactly one region appears — the one that row controls.
        const middle = screen.getByRole('button', { name: controlPattern(STAGE_ROWS[4]) });
        fireEvent.click(middle);

        expect(middle.getAttribute('aria-expanded')).toBe('true');
        const regions = screen.getAllByRole('region');
        expect(regions).toHaveLength(1);
        expect(regions[0].id).toBe(middle.getAttribute('aria-controls'));
        // Named by its own control, which is how it is announced on entry.
        expect(regions[0].getAttribute('aria-labelledby')).toBe(middle.id);
        expect(computeAccessibleName(regions[0])).toBe(computeAccessibleName(middle));

        cleanup();
      },
    );
  }, SLOW);
});

/* ══════════════════════════════════════════════════════════════════════════
 * retail-ui-simplification task 12.4 — the six absence accounts stay put
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Requirements 7.2, 19.6, 20.1, 20.2. design.md §6, §6.1 (Decision D4), §6.2.
 *
 * WHY THIS IS HERE WHEN TASK 12.4 MOVED NOTHING
 * ---------------------------------------------
 * Task 12.4 was planned as "move `SignalTrace.jsx`'s standing prose behind disclosure —
 * 1351 characters in 14 constants". Measured, the page declares 14 page-level string
 * constants holding 1276 characters, and only SIX of them are prose: the other eight are
 * the field-path tokens an absence reason is built from (`trace.dag_nodes.nodes[]` and its
 * siblings) plus `COUNT_NOUN`. All six are the sentences below, and every one of them is
 * design.md §6.1's COLUMN 2 — prose whose job is to account for one specific absent value.
 * §6.2's question, asked of each: remove the string and can a trader still tell this
 * absence from a different one, and still tell why? No. So not one of them moved, and this
 * declaration is what stops a later pass moving them, because the plan asked for exactly
 * that and the reason it must not happen is not visible from the constant's declaration.
 *
 * Each one renders ONLY in the state it describes, which is what makes putting it behind a
 * closed disclosure worse than leaving it standing: a trader looking at a stage that
 * reports nothing would see that it reports nothing and not why, which is the defect this
 * spec has spent twenty commits removing. `NO_DURATION_REASON` is the sharpest case — it
 * renders inside the stage's own `<button>` (`SignalTrace.jsx:1534`), so it is part of the
 * control's accessible name and there is no disclosure it could go behind at all.
 *
 * None of the six is standing prose either, on §5.5's definition or on any reading of it:
 * they need a signal selected AND a stage opened AND the absence they explain, so
 * `standing-prose.budget.js`'s 541 for this page never contained one of them and did not
 * move when this task landed. That measurement is recorded in the budget file's header.
 *
 * WHAT THIS ASSERTS, AND WHY IN THESE TWO CLAUSES
 * ----------------------------------------------
 *   1. **Present, character for character, in EVERY stage that should carry it.** Requirement
 *      19.6 permits moving prose and forbids rewording it, so the sentences are spelled out
 *      here in full rather than imported: a test that read them out of the page would agree
 *      with any edit, including a tightening. Asserted per stage region and not over the
 *      whole page, because two of these sentences render at two sites — a page-wide
 *      `textContent` check would let one site be hidden while the other kept it passing,
 *      which is exactly the false negative this assertion was rewritten to close.
 *   2. **Not behind a further collapsed control.** A `ds/Accordion` unmounts its closed
 *      body, so wrapping one of these would make it vanish from its stage and clause 1 would
 *      catch it. A disclosure that merely HIDES its body would not, so this walks every
 *      remaining `aria-expanded="false"` control and fails if the region it names carries
 *      one of the six — and the same walk checks no account is inside a `hidden` subtree.
 *
 * The fixture is one payload that reaches all six absences at once, which is possible
 * because they are absences of different things in one trace rather than alternatives.
 */

/**
 * The six, verbatim from `pages/SignalTrace.jsx`, with the state each one accounts for and
 * the stages that must carry it for {@link ABSENCE_PAYLOAD}.
 *
 * `where` is the declaration, so a failure names the constant rather than a sentence.
 * `stages` is the render SITES, enumerated: `REASON_NO_EXCHANGE_RESPONSE` renders in both
 * stage 7 and stage 8 and each is a separate account of a separate absence, so both are
 * named and hiding either one fails. `inControl` marks the one that renders inside the
 * stage's own `<button>` rather than in its region, which is why it needs no click.
 */
const ABSENCE_ACCOUNTS = Object.freeze([
  Object.freeze({
    where: 'NO_DURATION_REASON (:392)',
    state: 'a stage no `LATENCY_SOURCE` entry can report a duration for',
    inControl: true,
    stages: Object.freeze(['signal', 'order-decision', 'submission', 'position-update']),
    text:
      'Nothing in this response reports a duration for this stage. An unreported duration '
      + 'and a zero duration are different facts, so no figure is shown rather than a 0ms '
      + 'that would read as instant.',
  }),
  Object.freeze({
    where: 'REASON_NO_NODE_IO (:678)',
    state: 'a node reconstructed from the signal row, which carries no ports',
    stages: Object.freeze(['indicators']),
    text:
      'This node record reports no ports. Per-port inputs and outputs are recorded by the '
      + 'live trace engine only, so a node reconstructed from the signal row carries the '
      + 'evaluated reading instead.',
  }),
  Object.freeze({
    where: 'REASON_DECISION_ONLY (:931)',
    state: 'stage 4 with a decision but no LOGIC-category node behind it',
    stages: Object.freeze(['logic']),
    text:
      'The retained node trace carries no LOGIC-category node for this signal, so there are '
      + 'no per-node inputs or outputs to show. The decision above is the signal record\u2019s own.',
  }),
  Object.freeze({
    where: 'REASON_EVENT_REPEATS_SECTIONS (:965)',
    state: 'stage 5 whose event payload holds nothing stages 1 and 2 do not already show',
    stages: Object.freeze(['signal']),
    text:
      'The SIGNAL_GENERATED payload repeats `signal.indicators` and `signal.market_info`, '
      + 'which stages 1 and 2 render in full from the same row. It carries nothing else.',
  }),
  Object.freeze({
    where: 'REASON_CHECK_VERDICT_UNREPORTED (:1038)',
    state: 'a named risk check for which the server records no per-check verdict',
    stages: Object.freeze(['order-decision']),
    text:
      'The risk section names the checks it performed and reports one verdict over the whole '
      + 'set, so there is no per-check result recorded. The risk verdict above is that verdict.',
  }),
  Object.freeze({
    where: 'REASON_NO_EXCHANGE_RESPONSE (:1188)',
    state: 'stages 7 and 8 with no engine-observed exchange record retained',
    stages: Object.freeze(['submission', 'execution']),
    text:
      'No engine-observed exchange response is retained for this signal, so the timeline '
      + 'event above is the whole record of the venue\u2019s answer. The trace store keeps the '
      + 'engine\u2019s record for about an hour.',
  }),
]);

/**
 * One trace that is missing six different things at once.
 *
 * Every absence is reached through the projection rather than by handing `SignalTimeline` a
 * stage shape directly, so the states asserted below are states the server can actually
 * produce:
 *
 *   * `dag_nodes.nodes` holds ONE node, an `INDICATOR` with a reading and no ports — so
 *     stage 2 lists a node with nothing per-port to show (`REASON_NO_NODE_IO`), and stage 4
 *     takes `resolveLogic`'s "nodes retained, none of them LOGIC, decision present" branch
 *     (`signalTraceStages.js:444`) which is the one `REASON_DECISION_ONLY` exists for.
 *   * `risk_validation.detail.checks` is a `List[str]`, which is the shape the backend
 *     sends and the reason a per-check verdict cannot exist.
 *   * `execution` carries an `outcome` and NO `exchange_response`, so stages 7 and 8 are
 *     backed by their own timeline events while the engine record is what is missing.
 *   * the four stages absent from `LATENCY_SOURCE` have no duration slot to fill in any
 *     payload, which is why `NO_DURATION_REASON` needs nothing from this fixture.
 */
const ABSENCE_PAYLOAD = Object.freeze({
  signal: {
    decision: 'BUY',
    market_info: { symbol: 'BTC/USDT', timeframe: '15m', reported: { close: 63118 } },
    indicators: { rsi_14: 28.4 },
  },
  trace: {
    dag_nodes: {
      source: 'signals_row',
      nodes: [
        {
          node_id: 'rsi_14',
          node_type: 'INDICATOR',
          node_label: 'RSI 14',
          status: 'pass',
          reading: { rsi_14: 28.4 },
        },
      ],
    },
    ml_inference: {
      source: 'signal_trace_engine',
      applicable: true,
      detail: { model_id: 'm-1', confidence: 0.68 },
    },
    risk_validation: {
      source: 'signal_trace_engine',
      detail: { passed: true, blocked: false, checks: ['exposure', 'daily_loss'] },
    },
    execution: {
      source: 'signal_trace_engine',
      outcome: {
        execution_price: 63118,
        filled_quantity: 0.014,
        latency_ms: 12,
        executed_at: '2024-05-01T12:04:03Z',
      },
    },
  },
  timeline: KNOWN_EVENTS.map((name) => event(name, { decision: 'BUY', symbol: 'BTC/USDT' })),
  lifecycle_state_source: 'canonical',
});

describe('task 12.4: every absence account renders in the state it describes', () => {
  it('carries all six on the surface of their own state, none behind a second disclosure', () => {
    try {
      const trace = buildSignalTraceStages(ABSENCE_PAYLOAD);
      const { container } = render(
        <SignalTimeline stages={trace.stages} degraded={trace.degraded} />,
      );

      // The nine stage controls, opened one by one — the one click a trader pays. Matched by
      // ACCESSIBLE NAME rather than by taking every `aria-expanded` button on the page, so a
      // disclosure ADDED inside a stage body is not opened here and is left for clause 2 to
      // report. That is the difference between "one click" and "two". `computeAccessibleName`
      // over the collapsed rows for the reason the header gives: Testing Library's `name`
      // option is the same computation behind a full accessibility filter of the tree.
      const collapsed = [...container.querySelectorAll('button[aria-expanded][aria-controls]')];

      /** Stage id → the control that opens it and the region it names. */
      const byStage = new Map(
        STAGE_ROWS.map((row) => {
          const button = collapsed.find((candidate) =>
            controlPattern(row).test(computeAccessibleName(candidate)));
          expect(button, `stage ${row.number} (${row.name}) has no control to open`).toBeDefined();
          fireEvent.click(button);
          const region = document.getElementById(button.getAttribute('aria-controls'));
          expect(region, `stage ${row.number}'s aria-controls names no element`).not.toBeNull();
          return [row.id, { row, button, region }];
        }),
      );

      // Clause 1. Character for character (Requirement 19.6), per SITE, and non-vacuity for
      // the fixture at the same time: a payload that stopped reaching one of these absences
      // fails here rather than passing over a sentence that never rendered.
      const missing = [];

      for (const account of ABSENCE_ACCOUNTS) {
        for (const id of account.stages) {
          const stage = byStage.get(id);
          expect(stage, `${account.where} names no stage ${id}`).toBeDefined();
          // In the control for `NO_DURATION_REASON` — it is the marker's reason inside the
          // row's own `<button>`, so it is on screen before any click — and in the region
          // for the other five, which are the bodies of the sections they account for.
          const carrier = account.inControl === true ? stage.button : stage.region;
          if (!carrier.textContent.includes(account.text)) {
            missing.push(`${account.where} — absent from stage ${stage.row.number} `
              + `(${stage.row.name}), where it explains ${account.state}`);
          }
        }
      }

      expect(
        missing,
        'These absence accounts are not in the stage that should carry them. design.md\n'
          + '§6.1 puts every one of them in column 2: prose that accounts for one specific\n'
          + 'absent value, which Requirement 19.3 forbids shortening and 19.6 forbids\n'
          + 'rewording. If one moved behind a `ds/Accordion`, that is the move task 12.4\n'
          + 'considered and rejected — a trader reading a stage that reports nothing would\n'
          + `see that it reports nothing and not why:\n${missing.join('\n')}`,
      ).toEqual([]);

      // Clause 2. No collapsed control stands between the trader and an account, and no
      // account sits inside a `hidden` subtree once its own stage is open.
      const behind = [];

      for (const button of container.querySelectorAll('button[aria-expanded="false"]')) {
        const region = document.getElementById(button.getAttribute('aria-controls') ?? '');
        if (region === null) continue;
        for (const account of ABSENCE_ACCOUNTS) {
          if (region.textContent.includes(account.text)) {
            behind.push(`${account.where} — behind the collapsed "${computeAccessibleName(button)}"`);
          }
        }
      }

      for (const account of ABSENCE_ACCOUNTS) {
        const carriers = [...container.querySelectorAll('*')].filter(
          (element) =>
            element.textContent.includes(account.text)
            && ![...element.children].some((child) => child.textContent.includes(account.text)),
        );
        for (const carrier of carriers) {
          if (carrier.closest('[hidden]') !== null) {
            behind.push(`${account.where} — inside a hidden subtree`);
          }
        }
      }

      expect(
        behind,
        'An absence account is reachable only through a SECOND disclosure, or is hidden\n'
          + 'while the stage it belongs to is open. A string that explains an absence has to\n'
          + 'render in the state it describes (design.md §6.2), so one click on the stage is\n'
          + `the whole cost of reaching it:\n${behind.join('\n')}`,
      ).toEqual([]);
    } finally {
      cleanup();
    }
  });
});
