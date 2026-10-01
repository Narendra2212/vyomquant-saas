/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/design/entitlements.js — reading the server's entitlement answer
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * WHAT THIS MODULE IS, AND WHAT IT IS CAREFUL NOT TO BE
 * ----------------------------------------------------
 * This is a READER over the payload `GET /api/billing/entitlements` returns. It holds no plan
 * table, no price, and no rule about which plan may do what. Every one of those lives in
 * `backend_app/core/subscription_engine.py`, travels over the wire, and is rendered here.
 *
 * That boundary is the whole design, and it is the rule `src/api/modules/billing.js` already
 * states: *"the frontend NEVER decides subscription state or plan entitlements."* A second copy of
 * the ladder in this bundle would be a copy that can disagree with the server — and the disagreement
 * would always surface the same way, as a control the UI offered and the backend refused.
 *
 * So the only product knowledge in this file is COPY: the human label for a resource, and the
 * sentence to show when a figure cannot be read. Numbers, limits and permissions are read, never
 * derived.
 *
 * WHY THE FEATURE LIST IS SAFE TO READ CLIENT-SIDE
 * -----------------------------------------------
 * `entitlements.features` is the server's own list for the signed-in account, so rendering a
 * locked state from it is reporting the server's answer rather than making a decision. It is used
 * to decide what to SHOW. It is never the thing that permits an action: every gated action is
 * independently refused by the backend (`core/subscription_dependencies.py`), so editing this
 * bundle, the response in flight, or React state unlocks a button and nothing behind it.
 *
 * THE THREE KINDS OF LIMIT
 * ------------------------
 * The payload distinguishes them and so must any UI rendering it:
 *
 *   * **counted**   a live count of things that exist (strategies, exchange connections). Falls as
 *                   well as rises; "8 / 10" is a present-tense fact.
 *   * **metered**   consumption within `usage_period` (backtests, optimization runs, ML training).
 *                   Only rises, and resets on a month boundary — so it must be labelled
 *                   "this month" or a trader reads a monthly allowance as a lifetime one.
 *   * **unreadable** the server could not establish the figure and said why, in
 *                   `usage_unavailable[resource]`. NOT zero. `0 / 10` is a claim; this is the
 *                   absence of one, and the two look identical if a reader substitutes a default.
 */

import { fromNullable, unavailable } from './reported';

// ── Resource keys ──────────────────────────────────────────────────────────

/**
 * The resource keys the server meters, spelled once.
 *
 * Transcribed from `core.subscription_engine.Resource`, in its declaration order, for the same
 * reason `design/subscriptionState.js` transcribes `SubscriptionState`: a client-side guess at a
 * server enum spelling is the one mistake that never fires in testing. A key that reaches this
 * bundle and is not listed here is still rendered — see {@link resourceLabel} — it simply gets a
 * humanised fallback label instead of a curated one.
 */
export const RESOURCES = Object.freeze({
  STRATEGIES: 'strategies',
  PAPER_STRATEGIES: 'paper_strategies',
  LIVE_STRATEGIES: 'bots',
  EXCHANGE_CONNECTIONS: 'exchange_connections',
  TRADING_ACCOUNTS: 'trading_accounts',
  CUSTOM_INDICATORS: 'custom_indicators',
  STRATEGY_VERSIONS: 'strategy_versions',
  ML_MODELS: 'ml_models',
  MARKETPLACE_SUBSCRIPTIONS: 'marketplace_subscriptions',
  MARKETPLACE_LISTINGS: 'marketplace_published',
  BACKTESTS: 'backtests',
  OPTIMIZATIONS: 'optimizations',
  ML_TRAININGS: 'ml_trainings',
});

/**
 * The capability flags, spelled once.
 *
 * Transcribed from `core.subscription_engine.Feature`. Only the ones a surface actually gates on
 * are listed; the server sends more and {@link hasFeature} answers for any of them.
 */
export const FEATURES = Object.freeze({
  LIVE_TRADING: 'live_trading',
  ML_TRAINING: 'ml_training',
  ML_NODES: 'ml_nodes',
  OPTIMIZATION: 'optimization',
  ADVANCED_RISK: 'advanced_risk',
  PORTFOLIO_RISK: 'portfolio_risk',
  MARKETPLACE_BROWSE: 'marketplace_browse',
  MARKETPLACE_SUBSCRIBE: 'marketplace_subscribe',
  MARKETPLACE_PUBLISH: 'marketplace_publish',
  CREATOR_REVENUE: 'creator_revenue',
  ADVANCED_BUILDER: 'advanced_builder',
  STRATEGY_VERSIONING: 'strategy_versioning',
});

/** Human labels for the resources a usage panel renders. The only product copy in this file. */
const RESOURCE_LABELS = Object.freeze({
  [RESOURCES.STRATEGIES]: 'Strategies',
  [RESOURCES.PAPER_STRATEGIES]: 'Paper strategies',
  [RESOURCES.LIVE_STRATEGIES]: 'Live strategies',
  [RESOURCES.EXCHANGE_CONNECTIONS]: 'Exchange connections',
  [RESOURCES.TRADING_ACCOUNTS]: 'Trading accounts',
  [RESOURCES.CUSTOM_INDICATORS]: 'Custom indicators',
  [RESOURCES.STRATEGY_VERSIONS]: 'Strategy versions',
  [RESOURCES.ML_MODELS]: 'ML models',
  [RESOURCES.MARKETPLACE_SUBSCRIPTIONS]: 'Marketplace subscriptions',
  [RESOURCES.MARKETPLACE_LISTINGS]: 'Marketplace listings',
  [RESOURCES.BACKTESTS]: 'Backtests',
  [RESOURCES.OPTIMIZATIONS]: 'Optimization runs',
  [RESOURCES.ML_TRAININGS]: 'ML training runs',
});

/**
 * The label for a resource key.
 *
 * An unrecognised key is humanised rather than dropped: the server owns this vocabulary, so a key
 * this bundle has not heard of means the backend gained a resource before the frontend did, and
 * showing `Some new thing 0 / 5` is better than silently omitting a limit that is being enforced.
 *
 * @param {string} resource
 * @returns {string}
 */
export function resourceLabel(resource) {
  if (RESOURCE_LABELS[resource]) return RESOURCE_LABELS[resource];
  const text = String(resource ?? '').replace(/_/g, ' ').trim();
  return text === '' ? 'Allowance' : text.charAt(0).toUpperCase() + text.slice(1);
}

// ── The payload ────────────────────────────────────────────────────────────

/** The sentinel the server uses for a limit that is set per contract rather than published. */
export const CUSTOM_LIMIT = -1;

/**
 * Whether a limit value means "no published ceiling".
 *
 * Rendered as **Custom**, never as "Unlimited". The server's `-1` on the Enterprise tier means the
 * number is negotiated and recorded per account, and the backend enforces the Business figure
 * until one is — so "Unlimited" would be a promise the platform does not implement.
 *
 * @param {unknown} limit
 * @returns {boolean}
 */
export function isCustomLimit(limit) {
  return limit === CUSTOM_LIMIT || limit === Infinity;
}

/** The entitlements body, whichever envelope it arrived in. */
export function entitlementsBody(response) {
  return response?.data ?? response ?? null;
}

/**
 * Whether the account holds a capability.
 *
 * Reads the server's own `features` list. Returns `false` when the list is absent, which fails
 * CLOSED: a UI that cannot tell shows the locked state, and the locked state is recoverable (the
 * trader sees an upgrade panel) where a wrongly-unlocked control is not (they press it and get a
 * 403 they were given no reason to expect).
 *
 * @param {object|null} body A `/api/billing/entitlements` payload.
 * @param {string} feature One of {@link FEATURES}, or any flag the server sends.
 * @returns {boolean}
 */
export function hasFeature(body, feature) {
  const features = body?.features;
  if (!Array.isArray(features)) return false;
  return features.includes(feature);
}

/**
 * One resource's allowance, as a renderable shape.
 *
 * Returns `{ label, used, limit, isCustom, isMetered, exhausted, remaining, ratio }` where `used`
 * and `limit` are `Reported` unions — so a figure the server could not establish arrives carrying
 * the server's own reason and cannot be rendered as a number.
 *
 * `exhausted` is `false` whenever either figure is unreadable. Reporting "you are at your limit"
 * requires knowing both sides of it, and a guess in that direction would show a trader an upgrade
 * prompt for capacity they still have.
 *
 * @param {object|null} body
 * @param {string} resource
 */
export function allowance(body, resource) {
  const quotas = body?.quotas ?? {};
  const usage = body?.usage ?? {};
  const unreadable = body?.usage_unavailable ?? {};
  const metered = Array.isArray(body?.metered_resources)
    ? body.metered_resources
    : [];

  const rawLimit = quotas[resource];
  const isCustom = isCustomLimit(rawLimit);

  // The reason the server gave for an absent figure wins over any sentence invented here. When
  // the server sent neither a figure nor a reason, that is itself worth saying plainly.
  const absenceReason = unreadable[resource]
    ?? 'The server did not report this figure.';

  const used = Object.prototype.hasOwnProperty.call(unreadable, resource)
    ? unavailable(absenceReason)
    : fromNullable(usage[resource], absenceReason);

  const limit = isCustom
    ? unavailable('This allowance is set per agreement.')
    : fromNullable(rawLimit, 'The server did not report this limit.');

  const bothReadable = used.available && limit.available;
  const usedValue = used.available ? Number(used.value) : null;
  const limitValue = limit.available ? Number(limit.value) : null;

  return Object.freeze({
    resource,
    label: resourceLabel(resource),
    used,
    limit,
    isCustom,
    isMetered: metered.includes(resource),
    exhausted: bothReadable && limitValue > 0 && usedValue >= limitValue,
    // `0` is a real limit and means "not included in this plan", which is a different thing from
    // an exhausted allowance and gets different copy.
    notIncluded: limit.available && limitValue === 0,
    remaining: bothReadable ? Math.max(0, limitValue - usedValue) : null,
    ratio: bothReadable && limitValue > 0
      ? Math.min(1, Math.max(0, usedValue / limitValue))
      : null,
  });
}

/**
 * Every allowance the payload carries, in the order the server listed its quotas.
 *
 * Resources whose limit AND usage are both unreadable are still returned: the limit is being
 * enforced whether or not this client can read the figure, so hiding the row would hide the rule.
 *
 * @param {object|null} body
 */
export function allowances(body) {
  const quotas = body?.quotas;
  if (!quotas || typeof quotas !== 'object') return [];
  return Object.keys(quotas).map((resource) => allowance(body, resource));
}

/**
 * The resources the account currently exceeds, from the server's own `over_capacity` block.
 *
 * Only reachable after a downgrade. The server computes it because it is the one place that holds
 * both the measured usage and the new plan's limits; a client subtracting the two could disagree
 * with the gate that is actually refusing the writes.
 *
 * @param {object|null} body
 * @returns {Array<{resource: string, label: string, current: number, limit: number}>}
 */
export function overCapacity(body) {
  const block = body?.over_capacity;
  if (!block || typeof block !== 'object') return [];
  return Object.entries(block)
    .filter(([, value]) => Number.isFinite(Number(value?.current)) && Number.isFinite(Number(value?.limit)))
    .map(([resource, value]) => ({
      resource,
      label: resourceLabel(resource),
      current: Number(value.current),
      limit: Number(value.limit),
    }));
}

// ── Refusals that arrive on a 403 ──────────────────────────────────────────

/**
 * The structured entitlement refusal out of a rejected request, or `null`.
 *
 * `core/subscription_dependencies.py` answers a gated refusal with
 * `403 detail={code, message, current, limit, required_plan, required_tier, upgrade_message,
 * cta_label, contact_sales}`. This reads it off whichever envelope the transport produced —
 * `err.data.detail` is where `apiClient`'s `ApiError` puts a body, and `err.response.data.detail`
 * is the axios-native shape — the same two-place read `pages/Billing.jsx::writeFailureMessage`
 * does, and for the same reason: reading only one of them returned `undefined` for every rejection
 * this client produces.
 *
 * Membership is decided by the presence of a `code` string, so an ordinary error (a 500, a network
 * failure, a validation 422) is not mistaken for an upgrade opportunity.
 *
 * @param {unknown} err
 * @returns {object|null}
 */
export function readRefusal(err) {
  const candidates = [err?.data?.detail, err?.response?.data?.detail, err?.data, err?.detail];
  for (const candidate of candidates) {
    if (
      candidate
      && typeof candidate === 'object'
      && typeof candidate.code === 'string'
      && candidate.code.trim() !== ''
    ) {
      return candidate;
    }
  }
  return null;
}

/** The refusal codes that mean "a higher plan would allow this", for a caller that branches. */
export const REFUSAL_IS_UPGRADEABLE = (refusal) =>
  Boolean(refusal) && (typeof refusal.required_plan === 'string' || refusal.contact_sales === true);
