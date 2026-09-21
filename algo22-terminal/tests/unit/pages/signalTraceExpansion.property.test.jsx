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
