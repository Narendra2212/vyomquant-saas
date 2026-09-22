/**
 * ═══════════════════════════════════════════════════════════════════════════════════════
 * `ds/TradingEnvironmentBadge` — the badge's first test file
 * ═══════════════════════════════════════════════════════════════════════════════════════
 *
 * retail-ui-simplification task 2.1. design.md §4.2, §5.4. Requirements 9.1, 9.2, 18.2, 20.1.
 *
 * Written BEFORE task 2.2's treatment edit and observed passing against the unedited
 * primitive, so that 2.2's edit is *observed* rather than assumed.
 *
 * ═══ WHY THIS FILE DID NOT EXIST ═══
 *
 * `tests/unit/ds/` held 26 files and none of them was this one, while the badge reaches
 * eight pages and six primitives. What covered it was indirect:
 *
 *   * `tests/unit/dashboard-kill-switch.test.jsx:176` reads the dialog header's badge
 *     through `data-environment`, `data-environment-border`, `data-environment-icon`,
 *     the `span.sr-only + span` label and `title` — data attributes and text, never
 *     class strings. So the four treatment axes were asserted nowhere.
 *   * `tests/unit/liveTrading.test.jsx:287` covers `ENVIRONMENT UNCONFIRMED` for the
 *     unflagged null arm, and that it is neither `LIVE` nor `PAPER`.
 *   * `SIMULATED · SERVER LABEL UNAVAILABLE` — declared at
 *     `TradingEnvironmentBadge.jsx:108`, its reason recorded at `:61`–`:64` — was
 *     rendered by NO test in the suite. Requirement 9.2 forbids collapsing the two null
 *     arms, and until this file only ONE of them would have failed if they were
 *     collapsed. That is the hole block 1 closes.
 *
 * ═══ [CORRECTION TO §5.4] `data-environment` IS NOT DISTINCT ACROSS THE TWO NULL ARMS ═══
 *
 * Task 2.1 and design.md §5.4 both ask block 1 to assert "`data-environment` distinct on
 * each". Measured against the primitive: `environmentBadge`'s neutral arm returns
 * `id: 'UNCONFIRMED'` unconditionally (`TradingEnvironmentBadge.jsx:159`), so BOTH null
 * arms publish `data-environment="UNCONFIRMED"`. The distinction between them is carried
 * on three other axes — the label, the `title` long form and `data-environment-icon`
 * (`FlaskConical` when the server confirmed simulation, `HelpCircle` when it said
 * nothing). Block 1 therefore asserts the distinction where it actually lives, and pins
 * `data-environment` as shared on both arms so that a future attempt to split it is a
 * deliberate, visible change rather than a silent one. This is a correction about the
 * tree, in the same shape as the design's own [RE-MEASURED] notes; it does not weaken
 * the collapse net, because a collapse changes the label and the title.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';

import {
  ENVIRONMENT_BADGE_VARIANTS,
  TradingEnvironmentBadge,
  environmentBadge,
} from '../../../src/components/ds/TradingEnvironmentBadge';

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE COPY, RESTATED HERE ON PURPOSE
 *
 * These four strings are the ones Requirement 9.2 protects, so they are written out
 * rather than imported from the module under test: importing them would make the
 * assertion true by construction and a rewording would not fail anything.
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const SIMULATED_UNNAMED = 'SIMULATED · SERVER LABEL UNAVAILABLE';
const SIMULATED_UNNAMED_LONG =
  'The server reported these figures as simulated but did not name an execution environment.';
const UNCONFIRMED = 'ENVIRONMENT UNCONFIRMED';
const UNCONFIRMED_LONG = 'The server did not report an execution environment';

/** The screen-reader prefix `ds/Panel` set the precedent for. Requirement 20.1. */
const SR_PREFIX = 'Trading environment: ';

/**
 * Render one badge and read every axis a test here cares about.
 *
 * Scoped to the render's own container rather than `document`, because several `it`s
 * below mount both null arms at once in order to compare them as a pair.
 *
 * `label` uses `dashboard-kill-switch.test.jsx:176`'s selector verbatim — the span
 * directly after the screen-reader prefix, NOT `span:last-of-type`, which on the `strip`
 * variant reads the long form instead and leaves the label axis unasserted.
 */
const mount = (props) => {
  const { container } = render(<TradingEnvironmentBadge {...props} />);
  const root = container.querySelector('[data-environment]');
  expect(root, 'the badge rendered no root element').not.toBeNull();
  const prefix = root.querySelector('span.sr-only');
  expect(prefix, 'the badge rendered no screen-reader prefix').not.toBeNull();
  return {
    root,
    id: root.getAttribute('data-environment'),
    border: root.getAttribute('data-environment-border'),
    icon: root.getAttribute('data-environment-icon'),
    variant: root.getAttribute('data-environment-variant'),
    prefix: prefix.textContent,
    label: root.querySelector('span.sr-only + span').textContent,
    long: root.getAttribute('title'),
    text: root.textContent,
    classes: root.className.split(/\s+/).filter(Boolean),
  };
};

/** The two null arms, mounted together so they can be asserted against each other. */
const bothNullArms = () => ({
  flagged: mount({ environment: null, isSimulated: true }),
  silent: mount({ environment: null, isSimulated: false }),
});

afterEach(cleanup);

/* ══════════════════════════════════════════════════════════════════════════════════════
 * BLOCK 1 — BOTH NULL ARMS RENDER, AND RENDER DIFFERENTLY
 *
 * Asserted as a PAIR in a single `it` so that collapsing the two arms fails rather than
 * half-fails. `TradingEnvironmentBadge.jsx:61`–`:64` records why they are two facts and
 * not two spellings of one: the first says the server confirmed these figures are
 * simulated but not which simulator; the second says the server said nothing at all.
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('TradingEnvironmentBadge: the two null arms are two different facts (Requirement 9.2)', () => {
  it('renders both null arms, with different label, different long form and different icon', () => {
    const { flagged, silent } = bothNullArms();

    // The arm the whole suite was missing.
    expect(flagged.label).toBe(SIMULATED_UNNAMED);
    expect(flagged.long).toBe(SIMULATED_UNNAMED_LONG);
    expect(flagged.text).toContain(SIMULATED_UNNAMED);

    // The arm `liveTrading.test.jsx:287` already covers, restated at the primitive.
    expect(silent.label).toBe(UNCONFIRMED);
    expect(silent.long).toBe(UNCONFIRMED_LONG);
    expect(silent.text).toContain(UNCONFIRMED);

    // The pair. Every one of these is an inequality on purpose: this is the assertion a
    // collapse has to get past, and there is no arrangement of one label that satisfies
    // all three.
    expect(flagged.label).not.toBe(silent.label);
    expect(flagged.long).not.toBe(silent.long);
    // Shape, not just text: `FlaskConical` depicts the one fact the server did give us,
    // `HelpCircle` depicts having been told nothing (Requirement 20.5 — no state becomes
    // colour-only and no state becomes nothing).
    expect(flagged.icon).toBe('FlaskConical');
    expect(silent.icon).toBe('HelpCircle');
    expect(flagged.icon).not.toBe(silent.icon);

    // [CORRECTION TO §5.4, see the header] `data-environment` is NOT one of the axes that
    // separates them: the neutral arm publishes `UNCONFIRMED` for both. Pinned in both
    // directions so the day someone splits it is the day this line is edited on purpose.
    expect(flagged.id).toBe('UNCONFIRMED');
    expect(silent.id).toBe('UNCONFIRMED');
  });

  it('keeps both arms distinguishable in every variant', () => {
    for (const variant of ENVIRONMENT_BADGE_VARIANTS) {
      const flagged = mount({ environment: null, isSimulated: true, variant });
      const silent = mount({ environment: null, isSimulated: false, variant });
      expect(flagged.label, variant).toBe(SIMULATED_UNNAMED);
      expect(silent.label, variant).toBe(UNCONFIRMED);
      expect(flagged.label, variant).not.toBe(silent.label);
      cleanup();
    }
  });

  it('reports the two arms as different objects from `environmentBadge`, unrendered', () => {
    // The resolution is exported because Properties 12 and 22 read it without rendering.
    // A collapse performed in the resolver rather than in the JSX has to fail here too.
    const flagged = environmentBadge(null, true);
    const silent = environmentBadge(null, false);
    expect(flagged.label).toBe(SIMULATED_UNNAMED);
    expect(silent.label).toBe(UNCONFIRMED);
    expect(flagged.simulated).toBe(true);
    expect(silent.simulated).toBe(false);
    expect(flagged.named).toBe(false);
    expect(silent.named).toBe(false);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * BLOCK 2 — NEITHER ARM RESOLVES TO `LIVE` OR `PAPER`
 *
 * `semantic.js:265`'s rule as an assertion: defaulting to `LIVE` would be alarmist,
 * defaulting to `PAPER` would be dangerous, so the server's `null` is rendered as the
 * state it is rather than resolved into a value nobody sent. `resolveEnvironment`'s step
 * 3 — `isSimulated === true` with no named environment → `PAPER` — is exactly the default
 * this component must not take (`TradingEnvironmentBadge.jsx:76`–`:83`).
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('TradingEnvironmentBadge: an unreported environment is never resolved (Requirement 9.2)', () => {
  it('renders neither LIVE nor PAPER on either null arm', () => {
    const { flagged, silent } = bothNullArms();

    for (const [name, arm] of [['flagged', flagged], ['silent', silent]]) {
      expect(arm.id, name).toBe('UNCONFIRMED');
      expect(arm.id, name).not.toBe('LIVE');
      expect(arm.id, name).not.toBe('PAPER');
      expect(arm.text, name).not.toContain('LIVE');
      expect(arm.text, name).not.toContain('PAPER');
      expect(arm.long, name).not.toMatch(/\blive\b/i);
      expect(arm.long, name).not.toMatch(/\bpaper\b/i);
    }
  });

  it('does not resolve a value outside the three named environments', () => {
    // Anything the server sends that is not LIVE / PAPER / BACKTEST is unreported, not a
    // near-match to be rounded toward one of them.
    for (const value of ['', '   ', 'unknown', 'SIM', 'PAPERTRADING', 'live-ish', 7, {}, []]) {
      const arm = mount({ environment: value, isSimulated: false });
      expect(arm.id, JSON.stringify(value)).toBe('UNCONFIRMED');
      expect(arm.label, JSON.stringify(value)).toBe(UNCONFIRMED);
      cleanup();
    }
  });

  it('surfaces a LIVE + is_simulated contradiction instead of picking a winner', () => {
    // The one case where both fields are printed, because choosing LIVE hides a
    // simulation flag and choosing PAPER hides a live label.
    const conflict = mount({ environment: 'LIVE', isSimulated: true });
    expect(conflict.id).toBe('LIVE');
    expect(conflict.label).toBe('LIVE · SIMULATED');
    expect(conflict.long).toMatch(/neither can be dismissed/);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * BLOCK 3 — THE ACCESSIBLE NAME SURVIVES A TREATMENT CHANGE
 *
 * Requirement 20.1. Every variant keeps the screen-reader prefix and the `title` long
 * form, so the announcement stays "Trading environment: PAPER TRADING" rather than a
 * bare label floating beside a heading. Task 2.2 changes how the label looks; none of
 * this may move with it.
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const EVERY_STATE = Object.freeze([
  { props: { environment: 'LIVE' }, id: 'LIVE', label: 'LIVE' },
  { props: { environment: 'PAPER' }, id: 'PAPER', label: 'PAPER TRADING' },
  { props: { environment: 'BACKTEST' }, id: 'BACKTEST', label: 'BACKTEST' },
  { props: { environment: 'LIVE', isSimulated: true }, id: 'LIVE', label: 'LIVE · SIMULATED' },
  { props: { environment: null, isSimulated: true }, id: 'UNCONFIRMED', label: SIMULATED_UNNAMED },
  { props: { environment: null, isSimulated: false }, id: 'UNCONFIRMED', label: UNCONFIRMED },
]);

describe('TradingEnvironmentBadge: prefix, long form and data axes, in every variant (Requirement 20.1)', () => {
  it('carries the screen-reader prefix and a non-empty title on every state and variant', () => {
    for (const variant of ENVIRONMENT_BADGE_VARIANTS) {
      for (const state of EVERY_STATE) {
        const where = `${variant} / ${state.label}`;
        const arm = mount({ ...state.props, variant });

        expect(arm.prefix, where).toBe(SR_PREFIX);
        expect(arm.label, where).toBe(state.label);
        // The announcement is the prefix and the label together, in that order.
        expect(arm.text, where).toContain(`${SR_PREFIX}${state.label}`);
        // The long form is always available as the accessible description, whether or
        // not this variant also shows it.
        expect(arm.long, where).toBeTruthy();
        expect(arm.long.length, where).toBeGreaterThan(state.label.length);

        // The four published axes `dashboard-kill-switch.test.jsx` reads.
        expect(arm.id, where).toBe(state.id);
        expect(arm.border, where).toBeTruthy();
        expect(arm.icon, where).toBeTruthy();
        expect(arm.variant, where).toBe(variant);
        cleanup();
      }
    }
  });

  it('shows the long form visibly in the strip variant and as the tooltip elsewhere', () => {
    const strip = mount({ environment: null, isSimulated: false, variant: 'strip' });
    expect(strip.text).toContain(UNCONFIRMED_LONG);
    cleanup();

    for (const variant of ['chip', 'inline']) {
      const arm = mount({ environment: null, isSimulated: false, variant });
      expect(arm.text, variant).not.toContain(UNCONFIRMED_LONG);
      // Not lost, only moved: still reachable as the title.
      expect(arm.long, variant).toBe(UNCONFIRMED_LONG);
      cleanup();
    }
  });

  it('announces only when asked to, so nine badged panels do not announce nine times', () => {
    const quiet = mount({ environment: 'LIVE' });
    expect(quiet.root.getAttribute('role')).toBeNull();
    cleanup();
    const announced = mount({ environment: 'LIVE', announce: true });
    expect(announced.root.getAttribute('role')).toBe('status');
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * BLOCK 4 — THE TREATMENT AXES: SEEDED AT FOUR BY 2.1, REDUCED TO ONE BY 2.2
 *
 * ┌──────────────────────────────────────────────────────────────────────────────────┐
 * │ THIS IS THE ONE ASSERTION IN THIS SUITE INTENDED TO BE EDITED BY TASK 2.2, AND    │
 * │ TASK 2.2 HAS NOW EDITED IT. Task 2.1 seeded it at the treatment as it was —       │
 * │ `font-mono font-bold uppercase tracking-wide` — and observed all 12 tests green   │
 * │ against the unedited primitive. Task 2.2 reduced that to `font-mono` and lowered  │
 * │ this seed with it, in the same commit, so the reduction is observed rather than   │
 * │ assumed. Everything above this block passed unchanged in both states.            │
 * └──────────────────────────────────────────────────────────────────────────────────┘
 *
 * Read off the badge ROOT. [CORRECTION TO §5.4] §5.4 says "classes on the label
 * element": the label span (`:240`) carries no className of its own, because the
 * treatment is declared once on the root at `:230` and inherited by the label. The root
 * is therefore the element that declares the label's treatment, and the element task
 * 2.2 edits.
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/**
 * The treatment, after task 2.2: ONE axis. `whitespace-nowrap` is layout rather than
 * treatment and is not part of this list.
 *
 * `font-mono` is the one that stays, because it is the only one of the four that carries
 * meaning: every label this badge renders is a server-supplied literal, and mono is how
 * this app says "verbatim, from the server". `font-bold` and `tracking-wide` added weight
 * to a string that qualifies a figure rather than being one, and `uppercase` was
 * transforming nothing at all — all six labels are already uppercase in source.
 *
 * Asserted as an exact set rather than as three absences, so putting any of the three
 * back fails here rather than passing quietly.
 */
const TREATMENT_TODAY = Object.freeze(['font-mono']);

/**
 * Every class Tailwind offers on those four axes. The rendered class list is intersected
 * with this vocabulary and compared EXACTLY, so the assertion fails in both directions:
 * dropping an axis fails it, and so does swapping one axis for a neighbouring value on
 * the same axis (`font-semibold` for `font-bold`, `tracking-wider` for `tracking-wide`).
 */
const TREATMENT_VOCABULARY = Object.freeze([
  'font-mono', 'font-sans', 'font-serif',
  'font-thin', 'font-extralight', 'font-light', 'font-normal',
  'font-medium', 'font-semibold', 'font-bold', 'font-extrabold', 'font-black',
  'uppercase', 'lowercase', 'capitalize', 'normal-case',
  'tracking-tighter', 'tracking-tight', 'tracking-normal',
  'tracking-wide', 'tracking-wider', 'tracking-widest',
]);

const treatmentOf = (arm) => arm.classes.filter((c) => TREATMENT_VOCABULARY.includes(c)).sort();

describe('TradingEnvironmentBadge: the treatment axes (Requirement 9.1 — EDITED BY TASK 2.2)', () => {
  it('stacks exactly these text-treatment axes on the label, in every variant', () => {
    const expected = [...TREATMENT_TODAY].sort();
    for (const variant of ENVIRONMENT_BADGE_VARIANTS) {
      const arm = mount({ environment: null, isSimulated: false, variant });
      expect(treatmentOf(arm), variant).toEqual(expected);
      cleanup();
    }
  });

  it('applies the same treatment to every state, so no state is weighted above another', () => {
    const expected = [...TREATMENT_TODAY].sort();
    for (const state of EVERY_STATE) {
      const arm = mount(state.props);
      expect(treatmentOf(arm), state.label).toEqual(expected);
      cleanup();
    }
  });

  it('keeps the size on the variant and not on the treatment — `text-micro`, untouched by 2.2', () => {
    // The variant's own size class. Requirement 9.1 reduces the treatment stacked ON TOP
    // of this; it does not change the size, and no absolute px size appears here (the
    // font-size ratchet holds this file at zero).
    for (const variant of ENVIRONMENT_BADGE_VARIANTS) {
      const arm = mount({ environment: 'LIVE', variant });
      expect(arm.classes, variant).toContain('text-micro');
      expect(arm.classes.some((c) => /^text-\[/.test(c)), variant).toBe(false);
      cleanup();
    }
  });
});
