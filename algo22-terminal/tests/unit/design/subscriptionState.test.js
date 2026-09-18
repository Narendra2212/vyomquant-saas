/**
 * ═══════════════════════════════════════════════════════════════════════════
 * `design/subscriptionState` — the 7 → 4 collapse, row by row
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 26.1. design.md §7.9. Requirements 13.1, 13.3.
 *
 * WHAT IS PINNED HERE
 * ===================
 *   1. **The vocabulary is the backend's.** Seven inputs, the exact seven
 *      `backend_app/backend/marketplace/subscription_state.py` declares, and four outputs.
 *      Both frozen, because Property 24 (task 26.2) generates from these lists and a list a
 *      test could mutate is not a vocabulary.
 *   2. **Every one of §7.9's nine rows, by hand.** The table in the design document is the
 *      authority; this restates it as nine assertions so a row that changes has to change
 *      here too. `CANCELLED` gets its own case because it is the row that carries the
 *      reasoning.
 *   3. **Totality.** The seven, both server spellings of them, an absent row, `null`,
 *      `undefined`, numbers, arrays, objects, the empty string, whitespace, `'__proto__'`
 *      and arbitrary words all resolve to exactly one of the four badges. Nothing throws.
 *   4. **Fail closed.** Anything unrecognised lands on a badge that does not claim
 *      entitlement. This is the safety property: a badge defaulting to entitling tells a
 *      trader they may run something they may not.
 *   5. **The two decisions stay separate.** The badge moves only with
 *      `subscription.state`; `entitling` moves only with `entry.entitling`. Asserted by
 *      crossing them — including the `CANCELLED` case that proves they are different facts,
 *      and the disagreement case where neither is allowed to overrule the other.
 *   6. **No hue is declared here.** Each row's colour is the one `design/semantic.js`'s
 *      `statusToken` gives its `tokenState`, including the `neutral` rows, which reach the
 *      group through that module's documented fallback rather than through a vocabulary key.
 */

import { describe, it, expect } from 'vitest';

import { statusToken } from '../../../src/design/semantic';
import {
  BADGE_AVAILABLE,
  BADGE_EXPIRED,
  BADGE_PENDING_VERIFICATION,
  BADGE_SUBSCRIBED,
  ENTITLING_BADGES,
  SUBSCRIPTION_BADGES,
  SUBSCRIPTION_BADGE_ABSENT,
  SUBSCRIPTION_BADGE_LABEL,
  SUBSCRIPTION_BADGE_UNRECOGNISED,
  SUBSCRIPTION_STATES,
  badgeClaimsEntitlement,
  badgeForSubscriptionState,
  normaliseSubscriptionState,
  resolveSubscriptionView,
} from '../../../src/design/subscriptionState';

/** A `myStrategies()` entry carrying the server's `subscription_view()` triple. */
const entryWith = (subscription, rest = {}) => ({
  listing_id: 'lst_1',
  name: 'Momentum Breakout',
  subscription,
  ...rest,
});

/** The `subscription_view()` triple, exactly three keys. */
const view = (state, periodExpiry = null, renewalState = null) => ({
  state,
  period_expiry: periodExpiry,
  renewal_state: renewalState,
});

/**
 * §7.9's table, transcribed. `[serverState, badge, tokenState, showsExpiry]`.
 *
 * The two special rows are asserted separately — neither is reachable from a state word.
 */
const TABLE = [
  ['ACTIVE', BADGE_SUBSCRIBED, 'live', false],
  ['CANCELLED', BADGE_SUBSCRIBED, 'warning', true],
  ['PENDING', BADGE_PENDING_VERIFICATION, 'warning', false],
  ['PAYMENT_FAILED', BADGE_PENDING_VERIFICATION, 'warning', false],
  ['EXPIRED', BADGE_EXPIRED, 'error', false],
  ['SUSPENDED', BADGE_EXPIRED, 'error', false],
  ['REFUNDED', BADGE_EXPIRED, 'neutral', false],
];

/** Inputs no build can place. Every one must fail closed. */
const UNRECOGNISED = [
  null,
  undefined,
  '',
  '   ',
  'ACTIVE_SUBSCRIPTION',
  'not_subscribed',
  'trialing',
  'DELETED',
  '__proto__',
  'constructor',
  'toString',
  0,
  1,
  true,
  false,
  NaN,
  {},
  [],
  ['ACTIVE'],
  () => 'ACTIVE',
  Symbol('ACTIVE'),
];

// ---------------------------------------------------------------------------
// The vocabulary
// ---------------------------------------------------------------------------

describe('subscriptionState: the vocabulary', () => {
  it("names the backend's seven states, in its declaration order", () => {
    // Transcribed from subscription_state.py's SubscriptionState, which calls these "the 7
    // values, and the only values". §7.9's seven agree name for name.
    expect(SUBSCRIPTION_STATES).toEqual([
      'PENDING',
      'ACTIVE',
      'EXPIRED',
      'CANCELLED',
      'REFUNDED',
      'PAYMENT_FAILED',
      'SUSPENDED',
    ]);
  });

  it('names Requirement 13.1\'s four badges and no fifth', () => {
    expect(SUBSCRIPTION_BADGES).toEqual([
      'available',
      'subscribed',
      'pending-verification',
      'expired',
    ]);
    expect(Object.keys(SUBSCRIPTION_BADGE_LABEL).sort()).toEqual([...SUBSCRIPTION_BADGES].sort());
  });

  it('freezes both lists, so a generator cannot alter what it generates from', () => {
    expect(Object.isFrozen(SUBSCRIPTION_STATES)).toBe(true);
    expect(Object.isFrozen(SUBSCRIPTION_BADGES)).toBe(true);
    expect(Object.isFrozen(SUBSCRIPTION_BADGE_LABEL)).toBe(true);
    expect(Object.isFrozen(ENTITLING_BADGES)).toBe(true);
  });

  it('marks Subscribed, and only Subscribed, as the badge that claims entitlement', () => {
    expect(ENTITLING_BADGES).toEqual([BADGE_SUBSCRIBED]);
    expect(badgeClaimsEntitlement(BADGE_SUBSCRIBED)).toBe(true);
    expect(badgeClaimsEntitlement(BADGE_AVAILABLE)).toBe(false);
    expect(badgeClaimsEntitlement(BADGE_PENDING_VERIFICATION)).toBe(false);
    expect(badgeClaimsEntitlement(BADGE_EXPIRED)).toBe(false);
    // Total over a badge nobody declared.
    expect(badgeClaimsEntitlement('Subscribed')).toBe(false);
    expect(badgeClaimsEntitlement(null)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// The nine rows
// ---------------------------------------------------------------------------

describe('subscriptionState: §7.9\'s table', () => {
  it.each(TABLE)('maps %s onto %s in the %s token, expiry shown: %s', (
    state,
    badge,
    tokenState,
    showsExpiry,
  ) => {
    const resolved = badgeForSubscriptionState(state);

    expect(resolved.badge).toBe(badge);
    expect(resolved.tokenState).toBe(tokenState);
    expect(resolved.showsExpiry).toBe(showsExpiry);
    expect(resolved.label).toBe(SUBSCRIPTION_BADGE_LABEL[badge]);
  });

  it('covers all seven states and nothing else', () => {
    expect(TABLE.map(([state]) => state).sort()).toEqual([...SUBSCRIPTION_STATES].sort());
  });

  it('reads an absent subscription row as Available, neutral, no expiry', () => {
    expect(SUBSCRIPTION_BADGE_ABSENT).toMatchObject({
      badge: BADGE_AVAILABLE,
      label: 'Available',
      tokenState: 'neutral',
      showsExpiry: false,
      claimsEntitlement: false,
    });
  });

  it('fails an unrecognised state closed to Expired, neutral, non-entitling', () => {
    expect(SUBSCRIPTION_BADGE_UNRECOGNISED).toMatchObject({
      badge: BADGE_EXPIRED,
      label: 'Expired',
      tokenState: 'neutral',
      showsExpiry: false,
      claimsEntitlement: false,
    });
  });

  it('separates the two Expired hues rather than flattening them', () => {
    // REFUNDED is a completed commercial outcome, not a fault to act on; EXPIRED and
    // SUSPENDED are states a trader can do something about. Same badge word, different token.
    expect(badgeForSubscriptionState('REFUNDED').badge)
      .toBe(badgeForSubscriptionState('EXPIRED').badge);
    expect(badgeForSubscriptionState('REFUNDED').tokenState).toBe('neutral');
    expect(badgeForSubscriptionState('EXPIRED').tokenState).toBe('error');
  });

  it('separates the two Subscribed hues rather than flattening them', () => {
    expect(badgeForSubscriptionState('ACTIVE').tokenState).toBe('live');
    expect(badgeForSubscriptionState('CANCELLED').tokenState).toBe('warning');
  });

  it('never returns Available from a state word — Available is a claim about absence', () => {
    for (const state of [...SUBSCRIPTION_STATES, ...UNRECOGNISED]) {
      expect(badgeForSubscriptionState(state).badge).not.toBe(BADGE_AVAILABLE);
    }
  });

  it('freezes every row, so a consumer cannot repaint the table', () => {
    for (const state of SUBSCRIPTION_STATES) {
      expect(Object.isFrozen(badgeForSubscriptionState(state))).toBe(true);
    }
    expect(Object.isFrozen(SUBSCRIPTION_BADGE_ABSENT)).toBe(true);
    expect(Object.isFrozen(SUBSCRIPTION_BADGE_UNRECOGNISED)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// CANCELLED, the row that carries the reasoning
// ---------------------------------------------------------------------------

describe('subscriptionState: CANCELLED is Subscribed', () => {
  it('renders Subscribed in the warning token with the unchanged expiry', () => {
    // The backend keeps the entitlement alive to the unchanged expiry (backend Req 11.9), so
    // Expired would tell the trader they cannot use something they can.
    const resolved = resolveSubscriptionView(
      entryWith(view('CANCELLED', '2026-09-30T00:00:00+00:00', 'CANCELLED'), {
        entitling: true,
        unavailable_reason: null,
      }),
    );

    expect(resolved.badge).toBe(BADGE_SUBSCRIBED);
    expect(resolved.label).toBe('Subscribed');
    expect(resolved.tokenState).toBe('warning');
    expect(resolved.showsExpiry).toBe(true);
    expect(resolved.periodExpiry).toBe('2026-09-30T00:00:00+00:00');
    expect(resolved.renewalState).toBe('CANCELLED');
    expect(resolved.entitling).toBe(true);
  });

  it('is the only row that shows an expiry date', () => {
    const showing = SUBSCRIPTION_STATES.filter((s) => badgeForSubscriptionState(s).showsExpiry);
    expect(showing).toEqual(['CANCELLED']);
  });

  it('reports no expiry when the server carried none, rather than inventing one', () => {
    const resolved = resolveSubscriptionView(entryWith(view('CANCELLED', null, null)));

    expect(resolved.showsExpiry).toBe(true);
    expect(resolved.periodExpiry).toBeNull();
    expect(resolved.renewalState).toBeNull();
  });

  it('treats a blank expiry as absent, not as a present value', () => {
    const resolved = resolveSubscriptionView(entryWith(view('CANCELLED', '   ', '')));

    expect(resolved.periodExpiry).toBeNull();
    expect(resolved.renewalState).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Totality
// ---------------------------------------------------------------------------

describe('subscriptionState: totality', () => {
  it('answers for every one of the seven, in both server spellings', () => {
    for (const state of SUBSCRIPTION_STATES) {
      // `subscription_view()` publishes the enum spelling; the raw
      // `library_subscriptions.status` column is lower case, and the backend's own
      // `normalise_subscription_state` accepts both.
      const upper = badgeForSubscriptionState(state);
      const lower = badgeForSubscriptionState(state.toLowerCase());
      const padded = badgeForSubscriptionState(` ${state} `);
      const hyphenated = badgeForSubscriptionState(state.replace(/_/g, '-'));

      expect(lower).toBe(upper);
      expect(padded).toBe(upper);
      expect(hyphenated).toBe(upper);
      expect(SUBSCRIPTION_BADGES).toContain(upper.badge);
    }
  });

  it('normalises to one of the seven, or to null — never to a default', () => {
    expect(normaliseSubscriptionState('payment failed')).toBe('PAYMENT_FAILED');
    expect(normaliseSubscriptionState('payment-failed')).toBe('PAYMENT_FAILED');
    for (const value of UNRECOGNISED) {
      expect(normaliseSubscriptionState(value)).toBeNull();
    }
  });

  it('resolves every unrecognised input to exactly one badge, and it is non-entitling', () => {
    for (const value of UNRECOGNISED) {
      const resolved = badgeForSubscriptionState(value);

      expect(resolved).toBe(SUBSCRIPTION_BADGE_UNRECOGNISED);
      expect(SUBSCRIPTION_BADGES).toContain(resolved.badge);
      expect(resolved.claimsEntitlement).toBe(false);
    }
  });

  it('does not resolve a prototype key off the mapping object', () => {
    // A bare `STATE_ROWS[state]` index would find `__proto__`, `constructor` and `toString`
    // on Object.prototype and light a badge up on a word nobody sent.
    for (const key of ['__proto__', 'constructor', 'toString', 'valueOf', 'hasOwnProperty']) {
      expect(badgeForSubscriptionState(key)).toBe(SUBSCRIPTION_BADGE_UNRECOGNISED);
    }
  });

  it('answers for an entry of any shape without throwing', () => {
    const entries = [
      null,
      undefined,
      {},
      'lst_1',
      7,
      [],
      [entryWith(view('ACTIVE'))],
      entryWith(undefined),
      entryWith(null),
      entryWith({}),
      entryWith(view(undefined)),
      entryWith(5),
      entryWith('ACTIVE'),
      entryWith([]),
    ];

    for (const entry of entries) {
      const resolved = resolveSubscriptionView(entry);

      expect(SUBSCRIPTION_BADGES).toContain(resolved.badge);
      expect(typeof resolved.label).toBe('string');
      expect(typeof resolved.entitling).toBe('boolean');
      expect(Object.isFrozen(resolved)).toBe(true);
    }
  });

  it('reads an absent subscription as Available and a present-but-unreadable one as Expired', () => {
    // The two rows §7.9 puts at opposite ends of its table, and the confusion that matters:
    // "no row" is nothing wrong, "a row I cannot read" fails closed.
    expect(resolveSubscriptionView({}).badge).toBe(BADGE_AVAILABLE);
    expect(resolveSubscriptionView(entryWith(null)).badge).toBe(BADGE_AVAILABLE);
    expect(resolveSubscriptionView(entryWith(undefined)).badge).toBe(BADGE_AVAILABLE);
    expect(resolveSubscriptionView({}).hasSubscriptionRow).toBe(false);

    // `subscription_view()` returns `state: null` for exactly one reason: the persisted
    // status spelling fell outside the seven.
    expect(resolveSubscriptionView(entryWith(view(null))).badge).toBe(BADGE_EXPIRED);
    expect(resolveSubscriptionView(entryWith(view('trialing'))).badge).toBe(BADGE_EXPIRED);
    expect(resolveSubscriptionView(entryWith({})).badge).toBe(BADGE_EXPIRED);
    expect(resolveSubscriptionView(entryWith(5)).badge).toBe(BADGE_EXPIRED);
    expect(resolveSubscriptionView(entryWith(view(null))).hasSubscriptionRow).toBe(true);
  });

  it('reports serverState only when the server named one of the seven', () => {
    expect(resolveSubscriptionView(entryWith(view('active'))).serverState).toBe('ACTIVE');
    expect(resolveSubscriptionView(entryWith(view('trialing'))).serverState).toBeNull();
    expect(resolveSubscriptionView(entryWith(null)).serverState).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The badge and the entitlement are different facts
// ---------------------------------------------------------------------------

describe('subscriptionState: badge state and entitlement stay separate', () => {
  it('takes entitling from the server field, not from the state word', () => {
    for (const state of SUBSCRIPTION_STATES) {
      expect(resolveSubscriptionView(entryWith(view(state), { entitling: true })).entitling)
        .toBe(true);
      expect(resolveSubscriptionView(entryWith(view(state), { entitling: false })).entitling)
        .toBe(false);
      // Absent is not a default: a field the response does not carry is absent.
      expect(resolveSubscriptionView(entryWith(view(state))).entitling).toBe(false);
    }
  });

  it('takes the badge from the state word, not from the entitling field', () => {
    for (const [state, badge] of TABLE) {
      for (const entitling of [true, false, undefined]) {
        expect(resolveSubscriptionView(entryWith(view(state), { entitling })).badge).toBe(badge);
      }
    }
  });

  it('proves the two are different facts on CANCELLED', () => {
    // Ending, and entitled. Deriving either fact from the other gets this row wrong.
    const cancelled = resolveSubscriptionView(
      entryWith(view('CANCELLED', '2026-09-30T00:00:00+00:00'), { entitling: true }),
    );
    // ACTIVE is also entitled, and reads differently.
    const active = resolveSubscriptionView(entryWith(view('ACTIVE'), { entitling: true }));

    expect(cancelled.badge).toBe(active.badge);
    expect(cancelled.entitling).toBe(active.entitling);
    expect(cancelled.tokenState).not.toBe(active.tokenState);
  });

  it('lets the server disagree with itself without either fact overruling the other', () => {
    // A state word this build cannot place, alongside a server that says it entitles. The
    // badge still refuses to claim entitlement; the reported entitlement is still the
    // server's. Reconciling them here would be the frontend computing a fact it is given.
    const resolved = resolveSubscriptionView(
      entryWith(view('GRANDFATHERED'), { entitling: true }),
    );

    expect(resolved.badge).toBe(BADGE_EXPIRED);
    expect(resolved.claimsEntitlement).toBe(false);
    expect(resolved.entitling).toBe(true);
  });

  it('requires the identity of true — a truthy non-boolean is not the server saying yes', () => {
    for (const value of ['true', 1, {}, [], 'yes']) {
      expect(resolveSubscriptionView(entryWith(view('ACTIVE'), { entitling: value })).entitling)
        .toBe(false);
    }
  });

  it('surfaces the refusal code verbatim, and null when it entitles', () => {
    expect(
      resolveSubscriptionView(
        entryWith(view('SUSPENDED'), {
          entitling: false,
          unavailable_reason: 'SUBSCRIPTION_SUSPENDED',
        }),
      ).unavailableReason,
    ).toBe('SUBSCRIPTION_SUSPENDED');

    expect(
      resolveSubscriptionView(entryWith(view('ACTIVE'), { entitling: true, unavailable_reason: null }))
        .unavailableReason,
    ).toBeNull();

    // A blank code is an absent code.
    expect(
      resolveSubscriptionView(entryWith(view('ACTIVE'), { unavailable_reason: '  ' }))
        .unavailableReason,
    ).toBeNull();
  });

  it('reports an owned entry as Available and entitled, which is not a contradiction', () => {
    // `library_entries.py` writes `subscription: None, entitling: True` for an OWNED entry.
    // The badge reports the absence of a Subscription; the entitlement comes from ownership.
    const owned = resolveSubscriptionView({
      ownership: 'OWNED',
      subscription: null,
      entitling: true,
      unavailable_reason: null,
    });

    expect(owned.badge).toBe(BADGE_AVAILABLE);
    expect(owned.claimsEntitlement).toBe(false);
    expect(owned.entitling).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// The hue comes from semantic.js
// ---------------------------------------------------------------------------

describe('subscriptionState: no hue is declared here', () => {
  it('takes every row\'s colour from statusToken', () => {
    for (const state of SUBSCRIPTION_STATES) {
      const resolved = badgeForSubscriptionState(state);
      expect(resolved.token).toEqual(statusToken(resolved.tokenState));
    }
    expect(SUBSCRIPTION_BADGE_ABSENT.token).toEqual(statusToken('neutral'));
    expect(SUBSCRIPTION_BADGE_UNRECOGNISED.token).toEqual(statusToken('neutral'));
  });

  it('pins the neutral rows to the group statusToken\'s fallback gives them', () => {
    // `neutral` is not a key in semantic.js's vocabulary table; it resolves through that
    // module's documented total fallback. Asserted so a vocabulary edit cannot silently
    // repaint the Available and REFUNDED rows.
    expect(statusToken('neutral').group).toBe('neutral');
    expect(SUBSCRIPTION_BADGE_ABSENT.token.group).toBe('neutral');
    expect(badgeForSubscriptionState('REFUNDED').token.group).toBe('neutral');
  });

  it('gives each declared token state the matching semantic group', () => {
    expect(badgeForSubscriptionState('ACTIVE').token.group).toBe('live');
    expect(badgeForSubscriptionState('CANCELLED').token.group).toBe('warning');
    expect(badgeForSubscriptionState('PENDING').token.group).toBe('warning');
    expect(badgeForSubscriptionState('EXPIRED').token.group).toBe('error');
  });
});
