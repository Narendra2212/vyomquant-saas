/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Feature: vyomquant-ui-redesign, Property 1: The status colour mapping is
 * total and its semantic groups are distinct
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Task 5.2. **Validates: Requirements 1.4**
 *
 * `src/design/semantic.js` is the only module in the app permitted to turn a state into a
 * colour, which means every `ds/*` primitive spreads its result without checking it. That is
 * only safe if the mapping is *total*: there is no input — no unknown server string, no
 * `null`, no number, no inherited `Object.prototype` key — for which it can return
 * `undefined`. Totality is a claim over an unbounded input space, so it is asserted here
 * rather than by example.
 *
 * WHAT "DISTINCT" MEANS HERE, AND WHY
 * ----------------------------------
 * Distinctness is asserted on the **group name**, not on the colour value. `tokens.css`
 * deliberately gives `status.live`, `status.connected` and `status.profit` one green
 * (`#26A69A`) and `status.loss` and `status.error` one red (`#EF5350`), so four of the fifteen
 * pairs among the six requirement-named groups collide on **both** `fg` and `wash` — there are
 * two hue families, not six. `design.md` §3.2 states that collapse and §4.2 defends
 * `--color-env-live` reusing the loss hue on the same grounds. Requirement 1.4's text is about
 * single-sourcing ("exactly one semantic color mapping **each**"), not visual distinctness, so
 * what this property pins is that a consumer always receives a distinct `group` name backed by
 * its own declared token and can therefore tell the six apart even where two share a hue.
 * design.md's Property 1 carries the same note; do not "restore" a value-distinctness clause.
 *
 * `pnlToken` is property-tested here too. It is the other half of the same state → colour
 * mapping, and its zero-is-neutral rule is a deliberate correction of `PnLBadge`'s
 * `value >= 0` (which paints a flat position profit-green today), so it is worth pinning
 * across the whole numeric line rather than at a handful of sample values.
 *
 * The example-based suite for this module is `semantic.test.js`; this file does not repeat it.
 */

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { token } from '../../../src/design/tokens';
import {
  REQUIRED_STATUS_GROUPS,
  STATUS_GROUPS,
  STATUS_VOCABULARY,
  pnlToken,
  statusToken,
} from '../../../src/design/semantic';

/** design.md: "minimum 100 iterations per property". */
const RUNS = { numRuns: 250 };

/**
 * A label for a counterexample that cannot itself throw.
 *
 * `String(value)` is not safe over this input space: `fc.anything()` generates objects such as
 * `{toString: {}}`, whose own non-callable `toString` makes primitive conversion raise
 * `TypeError`. That would surface as a property failure caused by the assertion *message*
 * rather than by the subject under test — which is exactly the kind of false red a property
 * test must not produce.
 */
const show = (value) => {
  try {
    return typeof value === 'string' ? JSON.stringify(value) : `${value}`;
  } catch {
    return Object.prototype.toString.call(value);
  }
};

// ══════════════════════════════════════════════════════════════════════════════════════
// GENERATORS
//
// The input space is the union of what the app can actually hand this function — server
// status strings in whatever casing they arrive in — and everything a caller can hand it by
// accident. Both halves matter: the first is what the vocabulary table is for, the second is
// what totality is for.
// ══════════════════════════════════════════════════════════════════════════════════════

/**
 * `Object.prototype`'s own keys.
 *
 * `VOCABULARY` is an object literal, so these are all reachable through its prototype chain:
 * a naive `VOCABULARY[key]` returns `Object` for `'constructor'` and would then spread
 * `GROUP[Object]` — `undefined` — into the result. `statusToken` guards with
 * `hasOwnProperty`, and these inputs are what proves the guard is doing something.
 */
const PROTOTYPE_KEYS = ['constructor', 'toString', '__proto__', 'hasOwnProperty'];

/** Whitespace `String.prototype.trim` removes, including the non-breaking space. */
const WHITESPACE = [' ', '\t', '\n', '\r', '\f', '\v', '\u00a0', '\u2003'];

/** A declared vocabulary word, arriving in arbitrary casing with arbitrary padding. */
const declaredWord = fc
  .tuple(
    fc.constantFrom(...STATUS_VOCABULARY),
    fc.array(fc.boolean(), { minLength: 0, maxLength: 20 }),
    fc.array(fc.constantFrom(...WHITESPACE), { maxLength: 3 }),
    fc.array(fc.constantFrom(...WHITESPACE), { maxLength: 3 }),
  )
  .map(([word, upper, before, after]) => {
    const cased = word
      .split('')
      .map((c, i) => (upper[i] ? c.toUpperCase() : c))
      .join('');
    return `${before.join('')}${cased}${after.join('')}`;
  });

/** Whitespace and nothing else — normalises to the empty key. */
const whitespaceOnly = fc
  .array(fc.constantFrom(...WHITESPACE), { minLength: 1, maxLength: 6 })
  .map((cs) => cs.join(''));

/** Everything that is not a string. `fc.anything()` covers objects, arrays and the rest. */
const nonString = fc.oneof(
  fc.constant(null),
  fc.constant(undefined),
  fc.constant(NaN),
  fc.constant(0),
  fc.constant(-0),
  fc.integer(),
  fc.double(),
  fc.boolean(),
  fc.object(),
  fc.array(fc.string()),
  fc.anything().filter((v) => typeof v !== 'string'),
);

/** Any string at all, ASCII and beyond. */
const anyString = fc.oneof(
  fc.string(),
  fc.string({ unit: 'binary' }),
  fc.constant(''),
  whitespaceOnly,
  fc.constantFrom(...PROTOTYPE_KEYS),
  fc.constantFrom('NOT_A_STATE', 'live-ish', 'runnning', 'LIVE!', 'profit loss'),
);

/** The whole input space, in the proportions the assertions care about. */
const anyInput = fc.oneof(declaredWord, anyString, nonString);

/**
 * Membership of the declared vocabulary, read off the module's own export.
 *
 * This is not a second copy of the mapping — it answers "did the module declare this word",
 * not "which group does it belong to", which is the only thing the out-of-vocabulary
 * assertion needs to know.
 */
const DECLARED = new Set(STATUS_VOCABULARY);
const isDeclared = (value) =>
  typeof value === 'string' && DECLARED.has(value.trim().toLowerCase());

/** Anything the module never declared. */
const outOfVocabulary = anyInput.filter((v) => !isDeclared(v));

// ══════════════════════════════════════════════════════════════════════════════════════
// PROPERTY 1
// ══════════════════════════════════════════════════════════════════════════════════════

describe('Feature: vyomquant-ui-redesign, Property 1: The status colour mapping is total and its semantic groups are distinct', () => {
  it('returns a defined {group, fg, wash} whose tokens exist in the generated token set, for any input', () => {
    fc.assert(
      fc.property(anyInput, (input) => {
        const result = statusToken(input);

        expect(result, show(input)).toBeTypeOf('object');
        expect(result).not.toBeNull();
        expect(Object.keys(result).sort()).toEqual(['fg', 'group', 'wash']);

        const { group, fg, wash } = result;
        expect(STATUS_GROUPS, show(input)).toContain(group);

        // The tokens are not merely strings — they are *the* generated tokens. A hand-written
        // hex that happened to be the same colour would fail here, which is the point:
        // Requirement 1.4's single source is `tokens.css`, via `design/tokens.js`.
        const declared = token.status[group];
        expect(declared, group).toBeDefined();
        expect(fg).toBe(declared.fg);
        expect(wash).toBe(declared.wash);
      }),
      RUNS,
    );
  });

  it('resolves anything outside the declared vocabulary to neutral, never to undefined', () => {
    fc.assert(
      fc.property(outOfVocabulary, (input) => {
        const result = statusToken(input);
        expect(result.group, show(input)).toBe('neutral');
        expect(result.fg).toBe(token.status.neutral.fg);
        expect(result.wash).toBe(token.status.neutral.wash);
      }),
      RUNS,
    );
  });

  it('resolves every declared word to the same group whatever its casing or padding', () => {
    fc.assert(
      fc.property(declaredWord, (input) => {
        const result = statusToken(input);
        // A declared word never falls through to the fallback by accident of casing: it
        // resolves to whatever the table says, and to the same thing the canonical spelling
        // resolves to.
        expect(STATUS_GROUPS).toContain(result.group);
        expect(result).toEqual(statusToken(input.trim().toLowerCase()));
      }),
      RUNS,
    );
  });

  it('gives the six requirement-named groups six distinct names, each backed by a declared token', () => {
    // Asserted pairwise over generated pairs rather than by eye, so a future seventh group or
    // a renamed one cannot quietly collapse two of the six into each other.
    const index = fc.nat({ max: REQUIRED_STATUS_GROUPS.length - 1 });

    fc.assert(
      fc.property(index, index, (i, j) => {
        const a = statusToken(REQUIRED_STATUS_GROUPS[i]);
        const b = statusToken(REQUIRED_STATUS_GROUPS[j]);

        expect(token.status[a.group], REQUIRED_STATUS_GROUPS[i]).toBeDefined();
        expect(token.status[b.group], REQUIRED_STATUS_GROUPS[j]).toBeDefined();

        if (i === j) expect(a.group).toBe(b.group);
        else expect(a.group, `${REQUIRED_STATUS_GROUPS[i]} vs ${REQUIRED_STATUS_GROUPS[j]}`).not.toBe(b.group);
      }),
      RUNS,
    );

    expect(new Set(REQUIRED_STATUS_GROUPS.map((g) => statusToken(g).group)).size).toBe(6);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// pnlToken — the other half of the same mapping
// ══════════════════════════════════════════════════════════════════════════════════════

/** Finite numbers across the whole line, including the subnormals either side of zero. */
const finiteNumber = fc.oneof(
  fc.double({ noNaN: true, noDefaultInfinity: true }),
  fc.integer(),
  fc.constantFrom(0, -0, Number.MIN_VALUE, -Number.MIN_VALUE, Number.MAX_VALUE, -Number.MAX_VALUE),
);

/** Anything that is not a finite number. */
const notAFiniteNumber = fc.oneof(
  fc.constantFrom(NaN, Infinity, -Infinity, null, undefined, true, false),
  fc.string(),
  fc.integer().map(String),
  fc.object(),
  fc.array(fc.integer()),
  fc.anything().filter((v) => typeof v !== 'number'),
);

describe('Feature: vyomquant-ui-redesign, Property 1: The status colour mapping is total and its semantic groups are distinct (pnlToken)', () => {
  it('splits the numeric line into profit above zero, loss below, neutral at zero', () => {
    fc.assert(
      fc.property(finiteNumber, (value) => {
        const { group, fg, wash } = pnlToken(value);

        if (value > 0) expect(group, show(value)).toBe('profit');
        else if (value < 0) expect(group, show(value)).toBe('loss');
        else expect(group, show(value)).toBe('neutral');

        expect(fg).toBe(token.status[group].fg);
        expect(wash).toBe(token.status[group].wash);
      }),
      RUNS,
    );
  });

  it('treats zero and negative zero as neutral, not as profit', () => {
    // The deliberate correction of `ui-legacy/primitives.jsx`'s `PnLBadge`, which tests
    // `value >= 0` and therefore paints a flat position in profit green. `-0 > 0` is false and
    // `-0 === 0` is true, so both zeros take the neutral arm.
    for (const zero of [0, -0, 0.0, -0.0]) {
      expect(pnlToken(zero).group, show(zero)).toBe('neutral');
      expect(pnlToken(zero).fg).toBe(token.status.neutral.fg);
    }
  });

  it('treats every non-finite and non-numeric input as neutral', () => {
    fc.assert(
      fc.property(notAFiniteNumber, (value) => {
        const { group, fg, wash } = pnlToken(value);
        // An unreadable figure is not a gain. Rendering "not available" instead of `0` is
        // `ds/Metric`'s job; this function only refuses to colour it.
        expect(group, show(value)).toBe('neutral');
        expect(fg).toBe(token.status.neutral.fg);
        expect(wash).toBe(token.status.neutral.wash);
      }),
      RUNS,
    );
  });
});
