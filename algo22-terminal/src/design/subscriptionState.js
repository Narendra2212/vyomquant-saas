/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/design/subscriptionState.js — the 7 → 4 subscription-state collapse
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 26.1. design.md §7.9. Requirements 13.1, 13.3.
 * Property 24 (task 26.2) is asserted over this module.
 *
 * `pages/StrategyMarketplace.jsx` used to decide "are you subscribed?" inline, with
 * `subscription?.status === 'active'` — one of the seven states tested by string equality,
 * every other state rendering as no badge at all. This module is the whole decision,
 * declared once, so the six states that expression could not see each get an answer.
 *
 * TWO FACTS, TWO SOURCES, AND THEY ARE NOT THE SAME FACT
 * =====================================================
 * §7.9 asks for two things off one entry, and the entire point of this module is that it
 * never derives one from the other:
 *
 *   * **The badge** comes from `entry.subscription.state` — the server's `subscription_view()`
 *     triple `{state, period_expiry, renewal_state}`
 *     (`backend_app/backend/marketplace/library_entries.py::subscription_view`).
 *   * **The entitling decision** comes from `entry.entitling` / `entry.unavailable_reason`,
 *     which the server writes from `entitlement_resolver`'s own verdict and refusal code.
 *
 * The frontend computes neither. `CANCELLED` is the row that proves they are different
 * facts: the purchaser has stopped the next charge, so the subscription is *ending*, but
 * the backend keeps the entitlement alive to the **unchanged** expiry (backend Requirement
 * 11.9 — `cancel_subscription` writes no period column). A frontend that read "cancelled"
 * as "no longer entitled" would tell a trader they cannot run something they can, for up to
 * a month. So `CANCELLED` is **Subscribed**, in the `warning` token, with the expiry date
 * beside it: "ending soon" without lying about current access.
 *
 * THE COLLAPSE, AND WHERE THE SEVEN CAME FROM
 * ===========================================
 * `SubscriptionState` in `backend_app/backend/marketplace/subscription_state.py` declares
 * seven values and calls them "the 7 values, and the only values". {@link SUBSCRIPTION_STATES}
 * is transcribed from that enum in its declaration order, not from design.md — the same rule
 * `lib/signalTraceStages.js` follows for the timeline vocabulary, because a client-side guess
 * at a server enum spelling is the one mistake that can never fire in testing. The seven
 * agree with §7.9's seven exactly, name for name.
 *
 * | Server state          | UI badge             | Semantic token | Shows expiry |
 * | --------------------- | -------------------- | -------------- | ------------ |
 * | *(no subscription row)* | Available          | `neutral`      | no           |
 * | `ACTIVE`              | Subscribed           | `live`         | no           |
 * | `CANCELLED`           | Subscribed           | `warning`      | **yes**      |
 * | `PENDING`             | Pending Verification | `warning`      | no           |
 * | `PAYMENT_FAILED`      | Pending Verification | `warning`      | no           |
 * | `EXPIRED`             | Expired              | `error`        | no           |
 * | `SUSPENDED`           | Expired              | `error`        | no           |
 * | `REFUNDED`            | Expired              | `neutral`      | no           |
 * | *(unrecognised)*      | Expired              | `neutral`      | no           |
 *
 * The token is a property of the **row**, not of the badge: `subscribed` is `live` or
 * `warning` depending on which state produced it, and `expired` is `error` or `neutral`.
 * That is why the table is keyed by server state rather than by badge — collapsing to four
 * rows would have lost the two distinctions §7.9 spends them on.
 *
 * ABSENT ROW AND UNRECOGNISED STATE ARE DIFFERENT INPUTS
 * =====================================================
 * §7.9's first and last rows look adjacent and are opposites:
 *
 *   * **No subscription row** — `entry.subscription` is `null`, which is what
 *     `library_entries.py` writes for an entry the caller has not subscribed to. Nothing is
 *     wrong; there is simply nothing to report. → **Available**.
 *   * **A row whose state cannot be read** — `entry.subscription` is present but its `state`
 *     is `null`, an unknown word, or not a string at all (or `subscription` is not a record
 *     at all). `subscription_view` returns `state: null` for precisely one reason: the
 *     persisted `status` spelling fell outside the seven. A row exists and we cannot say what
 *     it means. → **Expired**, fail closed.
 *
 * Reading the second as the first is the dangerous confusion, so `badgeForSubscriptionState`
 * cannot return **Available** at all: Available is a claim about the absence of a row, and
 * only {@link resolveSubscriptionView}, which can see whether the row is there, may make it.
 *
 * FAILING CLOSED, PRECISELY
 * =========================
 * Every input resolves to exactly one of the four badges, and anything unrecognised resolves
 * to one that does not claim entitlement:
 *
 *   * `badge` is always one of {@link SUBSCRIPTION_BADGES}. Never `undefined`, never a fifth
 *     value, and no input throws — `null`, a number, an array, `'__proto__'`, the empty
 *     string and an arbitrary word all land on a row.
 *   * `claimsEntitlement` is a property of the badge and is true for **Subscribed** only. It
 *     is presentation: it says whether the badge *word* tells a trader they may run this.
 *     Unrecognised → Expired → false. That is the safety property Property 24 asserts.
 *   * `entitling` is `entry.entitling === true` and nothing else. Absent, `undefined`,
 *     `'true'`, `1` → false. The identity check is the same one `semantic.js`'s
 *     `resolveEnvironment` makes on `isSimulated`, for the same reason: a truthy non-boolean
 *     is not the server saying yes.
 *
 * The two can disagree — the server may report `entitling: true` alongside a state word this
 * build does not know. Nothing here reconciles them, because both are the server's to state:
 * the action stays enabled on the server's word, and the badge still refuses to claim
 * entitlement on a word it cannot read. Clamping either one would be this module inventing
 * the fact it exists to avoid inventing.
 *
 * NO HUE IS DECLARED HERE
 * ======================
 * `tokenState` is the *name* of a semantic state, and the colour is asked of
 * `design/semantic.js`'s `statusToken` — the one module permitted to turn a state into a
 * colour (Requirement 1.4). Note that `'neutral'` is not a key in that module's vocabulary
 * table; it resolves through `statusToken`'s documented total fallback, which returns the
 * `neutral` group for anything unrecognised. That is the intended answer and the sibling
 * suite pins it, so a future vocabulary edit cannot quietly repaint two of these rows.
 *
 * NO DATE IS INVENTED HERE
 * ========================
 * `periodExpiry` is the server's ISO-8601 text, verbatim, or `null`. `showsExpiry` says
 * whether §7.9 wants it beside the badge; the two are independent, so a `CANCELLED` row that
 * carries no expiry yields `{showsExpiry: true, periodExpiry: null}` and the page renders
 * the badge with no date rather than a fabricated one. Formatting is the page's — this module
 * holds no clock and no locale.
 */

import { statusToken } from './semantic';

// ── The seven inputs ───────────────────────────────────────────────────────────────────

/**
 * The seven `SubscriptionState` values, in the backend's declaration order.
 *
 * Transcribed from `backend_app/backend/marketplace/subscription_state.py`. Frozen so
 * Property 24 can generate from the same list the mapping is built from.
 */
export const SUBSCRIPTION_STATES = Object.freeze([
  'PENDING',
  'ACTIVE',
  'EXPIRED',
  'CANCELLED',
  'REFUNDED',
  'PAYMENT_FAILED',
  'SUSPENDED',
]);

// ── The four outputs ───────────────────────────────────────────────────────────────────

/** No subscription row. Nothing is wrong; there is nothing to report. */
export const BADGE_AVAILABLE = 'available';

/** `ACTIVE` or `CANCELLED`. The only badge that claims entitlement. */
export const BADGE_SUBSCRIBED = 'subscribed';

/** `PENDING` or `PAYMENT_FAILED`. Payment has not been confirmed. */
export const BADGE_PENDING_VERIFICATION = 'pending-verification';

/** `EXPIRED`, `SUSPENDED`, `REFUNDED`, and anything unreadable. */
export const BADGE_EXPIRED = 'expired';

/** The four Requirement 13.1 badges. Frozen; the mapping cannot produce a fifth. */
export const SUBSCRIPTION_BADGES = Object.freeze([
  BADGE_AVAILABLE,
  BADGE_SUBSCRIBED,
  BADGE_PENDING_VERIFICATION,
  BADGE_EXPIRED,
]);

/** Badge → its rendered word. The only copy this mapping owns. */
export const SUBSCRIPTION_BADGE_LABEL = Object.freeze({
  [BADGE_AVAILABLE]: 'Available',
  [BADGE_SUBSCRIBED]: 'Subscribed',
  [BADGE_PENDING_VERIFICATION]: 'Pending Verification',
  [BADGE_EXPIRED]: 'Expired',
});

/**
 * The badges whose word tells a trader they may run this strategy.
 *
 * Exactly one. **Available** is not on this list: it reports that no subscription exists,
 * which is the opposite of an entitlement, and an owned entry's entitlement arrives on
 * `entry.entitling` like every other.
 */
export const ENTITLING_BADGES = Object.freeze([BADGE_SUBSCRIBED]);

/**
 * Whether a badge's word claims entitlement. Total: an unknown badge is false.
 *
 * @param {unknown} badge
 * @returns {boolean}
 */
export function badgeClaimsEntitlement(badge) {
  return ENTITLING_BADGES.includes(badge);
}

// ── The mapping ────────────────────────────────────────────────────────────────────────

/**
 * @typedef {{
 *   badge: 'available'|'subscribed'|'expired'|'pending-verification',
 *   label: string,
 *   tokenState: 'neutral'|'live'|'warning'|'error',
 *   token: { group: string, fg: string, wash: string },
 *   showsExpiry: boolean,
 *   claimsEntitlement: boolean
 * }} SubscriptionBadgeRow
 */

/**
 * One row of §7.9's table, with its label and hue resolved once at module load.
 *
 * @param {string} badge
 * @param {string} tokenState
 * @param {boolean} showsExpiry
 * @returns {SubscriptionBadgeRow}
 */
function row(badge, tokenState, showsExpiry = false) {
  return Object.freeze({
    badge,
    label: SUBSCRIPTION_BADGE_LABEL[badge],
    tokenState,
    token: statusToken(tokenState),
    showsExpiry,
    claimsEntitlement: badgeClaimsEntitlement(badge),
  });
}

/** §7.9 row 1 — `entry.subscription` is absent. Not reachable from a state word. */
export const SUBSCRIPTION_BADGE_ABSENT = row(BADGE_AVAILABLE, 'neutral');

/** §7.9's last row — a row exists and its state cannot be read. Fails closed. */
export const SUBSCRIPTION_BADGE_UNRECOGNISED = row(BADGE_EXPIRED, 'neutral');

/**
 * The seven states → their rows. Keyed by the server's spelling.
 *
 * `REFUNDED` shares **Expired** with `EXPIRED` and `SUSPENDED` but takes the `neutral`
 * token, not `error`: a reversed settlement is a completed commercial outcome, not a fault
 * for the trader to act on. `EXPIRED` and `SUSPENDED` keep `error` because both are states a
 * trader can do something about.
 */
const STATE_ROWS = Object.freeze({
  PENDING: row(BADGE_PENDING_VERIFICATION, 'warning'),
  ACTIVE: row(BADGE_SUBSCRIBED, 'live'),
  EXPIRED: row(BADGE_EXPIRED, 'error'),
  // The one non-obvious row. Entitled to the unchanged expiry, so the date is shown.
  CANCELLED: row(BADGE_SUBSCRIBED, 'warning', true),
  REFUNDED: row(BADGE_EXPIRED, 'neutral'),
  PAYMENT_FAILED: row(BADGE_PENDING_VERIFICATION, 'warning'),
  SUSPENDED: row(BADGE_EXPIRED, 'error'),
});

/**
 * A server state word → one of the seven, or `null` when it is outside them.
 *
 * Mirrors `subscription_state.normalise_subscription_state` step for step — trim, `-` and
 * space to `_`, uppercase — so the enum spelling `subscription_view` publishes (`ACTIVE`)
 * and the lower-case `library_subscriptions.status` column value the
 * `GET /api/library/{id}/subscribe` read returns verbatim (`active`) resolve to the same
 * state. Accepting both is not leniency: they are the same value in two of the server's own
 * spellings, and the backend's own reader accepts both for this reason.
 *
 * `null` rather than a throw, and `null` rather than a default: the caller decides what an
 * unreadable state means, and here that decision is to fail closed.
 *
 * @param {unknown} value
 * @returns {string|null} One of {@link SUBSCRIPTION_STATES}, or `null`.
 */
export function normaliseSubscriptionState(value) {
  if (typeof value !== 'string') return null;
  const text = value.trim().replace(/[-\s]/g, '_').toUpperCase();
  return SUBSCRIPTION_STATES.includes(text) ? text : null;
}

/**
 * A server state word → its badge row. Total, and never **Available**.
 *
 * Anything outside the seven — `null`, `undefined`, a number, an object, `'__proto__'`, the
 * empty string, a state a future backend adds — is {@link SUBSCRIPTION_BADGE_UNRECOGNISED},
 * which is **Expired** and does not claim entitlement. The lookup goes through
 * `normaliseSubscriptionState`'s allow-list rather than indexing `STATE_ROWS` directly, so
 * a prototype key cannot resolve to an inherited property and light a badge up.
 *
 * @param {unknown} state
 * @returns {SubscriptionBadgeRow}
 */
export function badgeForSubscriptionState(state) {
  const resolved = normaliseSubscriptionState(state);
  return resolved === null ? SUBSCRIPTION_BADGE_UNRECOGNISED : STATE_ROWS[resolved];
}

// ── The entry-level resolution ─────────────────────────────────────────────────────────

/**
 * Server text, or `null` when the response does not carry it. Never the empty string —
 * a blank is an absent value wearing a present value's clothes.
 *
 * @param {unknown} value
 * @returns {string|null}
 */
function serverText(value) {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  return text === '' ? null : text;
}

/**
 * @typedef {SubscriptionBadgeRow & {
 *   serverState: string|null,
 *   periodExpiry: string|null,
 *   renewalState: string|null,
 *   entitling: boolean,
 *   unavailableReason: string|null,
 *   hasSubscriptionRow: boolean
 * }} SubscriptionView
 */

/**
 * One Marketplace entry → the badge to render and the entitlement the server reported.
 *
 * Reads exactly five server fields and derives nothing from any other: `subscription.state`,
 * `subscription.period_expiry`, `subscription.renewal_state`, `entitling` and
 * `unavailable_reason`. Total over every input — `null`, `undefined`, a string, an array, an
 * entry with no `subscription` key, a `subscription` that is a number — because a listing
 * that fails to produce a badge is a listing with no badge, and Property 24 requires every
 * listing to render exactly one.
 *
 * `serverState` is the **normalised** state when it is one of the seven and `null`
 * otherwise, so a caller can tell "the server named a state I know" from "it did not"
 * without re-deriving the badge. The raw text is deliberately not surfaced: nothing should
 * render a state word this module could not place.
 *
 * @param {unknown} entry A `browse()` / `myStrategies()` entry, or anything at all.
 * @returns {SubscriptionView}
 */
export function resolveSubscriptionView(entry) {
  const source = entry !== null && typeof entry === 'object' ? entry : {};
  const subscription = source.subscription;

  /*
   * A row is claimed by anything other than absence. `null` and `undefined` are the
   * server saying there is no Subscription (`library_entries.py` writes `subscription:
   * None` for an unsubscribed entry) — those are §7.9's Available row. Anything else,
   * including a value that is not even a record, is a row this build cannot read, and
   * reading it as Available would invite a second purchase of a live subscription.
   */
  const hasSubscriptionRow = subscription !== null && subscription !== undefined;

  const badgeRow = hasSubscriptionRow
    ? badgeForSubscriptionState(subscription.state)
    : SUBSCRIPTION_BADGE_ABSENT;

  return Object.freeze({
    ...badgeRow,
    serverState: hasSubscriptionRow ? normaliseSubscriptionState(subscription.state) : null,
    periodExpiry: hasSubscriptionRow ? serverText(subscription.period_expiry) : null,
    renewalState: hasSubscriptionRow ? serverText(subscription.renewal_state) : null,
    // The server's decision, never this module's. Identity check on purpose.
    entitling: source.entitling === true,
    unavailableReason: serverText(source.unavailable_reason),
    hasSubscriptionRow,
  });
}
