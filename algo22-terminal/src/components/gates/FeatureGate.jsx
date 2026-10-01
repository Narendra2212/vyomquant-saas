/**
 * ═══════════════════════════════════════════════════════════════════════════
 * FeatureGate / PlanGate / UsageLimit — rendering the server's verdict
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * THE ONE RULE THESE THREE COMPONENTS OBEY
 * ---------------------------------------
 * They decide what to SHOW. They never decide what is ALLOWED.
 *
 * Every capability they gate is independently refused by the backend
 * (`core/subscription_dependencies.py`), so editing this bundle, the response in flight, local
 * storage or React state unlocks a rendering and nothing behind it. The request still 403s. That
 * separation is why a gate can be driven from a payload the client holds without becoming a
 * security control — and it is the reason none of these components ever compares a plan name to a
 * plan name. They read `entitlements.features`, which is the server's own answer for this account.
 *
 * WHY THE COPY IS NOT IN THIS FILE
 * --------------------------------
 * `GET /api/billing/entitlements` carries `locked_features[feature]` and
 * `limit_refusals[resource]` — the same `EntitlementRefusal` payloads the 403s carry, with the
 * headline, the explanation and the button label already composed by the backend from the one plan
 * catalogue. A gate looks its refusal up and hands it to {@link UpgradePrompt}.
 *
 * The alternative — composing "Upgrade to Pro Quant for ₹999" here — needs a local plan ladder and
 * a local price list, and both would be free to drift from the catalogue that enforces the limit
 * and the gateway that takes the money. A bundle shipped before a price change would then quote the
 * old figure on a button that charges the new one.
 *
 * FAILING CLOSED, AND WHY `fallback` IS NOT `null`
 * ----------------------------------------------
 * While entitlements are unknown — first load, or a read that failed — a gate renders its locked
 * state, not its children. The two directions of error are not symmetric: a wrongly-locked control
 * shows the trader an upgrade panel they can question, while a wrongly-unlocked one invites them to
 * press a button that fails with a 403 they were given no reason to expect. On a surface that places
 * real orders, the recoverable error is the right one to make.
 */

import React from 'react';

import { useEntitlements } from '../../hooks/useEntitlements';
import { allowance, hasFeature } from '../../design/entitlements';
import { LockedCommand, UpgradePrompt } from './UpgradePrompt';

/** True for a string with visible content. */
const hasText = (value) => typeof value === 'string' && value.trim() !== '';

/**
 * The refusal payload for a locked feature, as the server composed it.
 *
 * `null` when the feature is not locked, or when the server sent no copy for it — in which case the
 * gate still hides the children (it is locked either way) but renders no prompt, because a prompt
 * with no sentence in it is an advertisement rather than an explanation.
 */
function lockedFeatureRefusal(body, feature) {
  const locked = body?.locked_features;
  if (!locked || typeof locked !== 'object') return null;
  const refusal = locked[feature];
  return refusal && typeof refusal === 'object' ? refusal : null;
}

/**
 * Show ``children`` only when the account's plan includes ``feature``.
 *
 * @param {object} props
 * @param {string} props.feature One of `design/entitlements.FEATURES`.
 * @param {React.ReactNode} props.children What the capability looks like when available.
 * @param {'panel'|'inline'|'banner'|'hidden'|'command'} [props.variant] How the locked state
 *   renders. `hidden` renders nothing at all — for a surface where a locked panel would be noise
 *   rather than information. `command` renders a disabled control carrying the reason.
 * @param {string} [props.title] Overrides the server's headline. For naming the feature on a
 *   surface where the limit is not self-evident. Never for restating a price.
 * @param {React.ReactNode} [props.lockedChildren] Extra context inside the locked panel.
 * @param {React.ReactNode} [props.commandLabel] The label for `variant="command"`.
 */
export function FeatureGate({
  feature,
  children,
  variant = 'panel',
  title,
  lockedChildren,
  commandLabel,
  className = '',
}) {
  const { body, isReady } = useEntitlements();

  // Unknown entitlements render as locked. See the docblock on failing closed.
  const permitted = isReady && hasFeature(body, feature);
  if (permitted) return <>{children}</>;

  if (variant === 'hidden') return null;

  const refusal = lockedFeatureRefusal(body, feature);

  if (variant === 'command') {
    return (
      <LockedCommand refusal={refusal} className={className}>
        {commandLabel ?? children}
      </LockedCommand>
    );
  }

  return (
    <UpgradePrompt
      refusal={refusal}
      variant={variant === 'inline' || variant === 'banner' ? variant : 'panel'}
      title={title}
      className={className}
    >
      {lockedChildren}
    </UpgradePrompt>
  );
}

/**
 * Show ``children`` only to an account at or above ``minimumTier`` on the ladder.
 *
 * PREFER {@link FeatureGate}. A plan name is a proxy for a capability, and a proxy goes stale the
 * moment a capability moves between plans — a `PlanGate minimumTier="PRO_QUANT"` around the ML
 * builder keeps hiding it from Trader even after Trader gains ML, and nothing fails loudly when it
 * does. `FeatureGate` asks the question the backend actually answers.
 *
 * This exists for the cases where the thing being gated is the PLAN itself rather than a capability:
 * a creator-economy explainer addressed to paid tiers, a capacity table row. The comparison is made
 * on the server's `rank`, which arrives on the plan catalogue — not on an ordering declared here.
 *
 * @param {object} props
 * @param {string} props.minimumTier A `PlanTier` value: `FREE`, `TRADER`, `PRO_QUANT`, `BUSINESS`,
 *   `ENTERPRISE`.
 * @param {React.ReactNode} props.children
 * @param {React.ReactNode} [props.fallback] Rendered instead when the account is below the tier.
 */
export function PlanGate({ minimumTier, children, fallback = null }) {
  const { body, isReady } = useEntitlements();

  if (!isReady) return fallback;

  // The ladder order, as the server declares it on every plan it sends. Transcribed here only as a
  // LOOKUP for the requested tier name, because a tier name is all a call site can pass; the
  // account's own position comes from the payload.
  const ORDER = ['FREE', 'TRADER', 'PRO_QUANT', 'BUSINESS', 'ENTERPRISE'];
  const wanted = ORDER.indexOf(String(minimumTier ?? '').toUpperCase());
  const held = ORDER.indexOf(String(body?.tier ?? '').toUpperCase());

  // An unrecognised tier on either side fails closed rather than defaulting to permitted.
  if (wanted < 0 || held < 0) return fallback;
  return held >= wanted ? <>{children}</> : fallback;
}

/**
 * A measured allowance, rendered as a bar, with the server's prompt when it is exhausted.
 *
 * Renders NOTHING numeric when the server could not establish the figure: `used` and `limit` arrive
 * as `Reported` unions and the absent arm carries the server's own reason, so an unreadable usage
 * figure shows that reason rather than `0 / 10`. The two look identical on screen and mean opposite
 * things — the whole point of `design/reported.js`.
 *
 * @param {object} props
 * @param {string} props.resource One of `design/entitlements.RESOURCES`.
 * @param {boolean} [props.showPromptWhenExhausted] Render the server's upgrade prompt under the bar
 *   once the allowance is spent. Default `true`.
 * @param {string} [props.className]
 */
export function UsageLimit({ resource, showPromptWhenExhausted = true, className = '' }) {
  const { body, isReady } = useEntitlements();

  if (!isReady) return null;

  const entry = allowance(body, resource);
  const refusal = body?.limit_refusals?.[resource] ?? null;

  const usedText = entry.used.available ? Number(entry.used.value).toLocaleString('en-IN') : null;
  const limitText = entry.isCustom
    ? 'Custom'
    : entry.limit.available
      ? Number(entry.limit.value).toLocaleString('en-IN')
      : null;

  // The reason to show when either half is missing. The server's sentence, never one composed here.
  const absence = !entry.used.available
    ? entry.used.reason
    : !entry.limit.available && !entry.isCustom
      ? entry.limit.reason
      : null;

  return (
    <div
      data-gate="usage-limit"
      data-gate-resource={resource}
      className={`flex flex-col gap-1.5 ${className}`.trim()}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-small text-content-secondary">
          {entry.label}
          {/* A metered allowance MUST say "this month" or a trader reads a monthly figure as a
              lifetime total and thinks they have spent far less than they have. */}
          {entry.isMetered ? <span className="text-content-muted"> this month</span> : null}
        </span>
        {absence ? (
          <span className="text-small text-content-muted" title={absence}>
            Not available
          </span>
        ) : (
          <span className="font-mono text-small tabular-nums text-content-primary">
            {usedText}
            <span className="text-content-muted"> / {limitText}</span>
          </span>
        )}
      </div>

      {/* The bar is drawn only from a measured ratio. A custom allowance and an unreadable figure
          both have no ratio, and a bar at an invented width would be a claim about capacity. */}
      {entry.ratio === null ? null : (
        <div
          className="h-1 w-full overflow-hidden rounded-full bg-surface-inset"
          role="progressbar"
          aria-valuenow={Math.round(entry.ratio * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`${entry.label} used`}
        >
          <div
            className={`h-full rounded-full ${entry.exhausted ? 'bg-status-warning' : 'bg-brand'}`}
            style={{ width: `${Math.round(entry.ratio * 100)}%` }}
          />
        </div>
      )}

      {showPromptWhenExhausted && entry.exhausted && refusal ? (
        <UpgradePrompt refusal={refusal} variant="inline" className="mt-0.5" />
      ) : null}
    </div>
  );
}

/**
 * The at-capacity prompt for one resource, with no bar.
 *
 * For a surface that already shows the count elsewhere and needs only the refusal — a strategy list
 * header, a "new strategy" button's neighbour. Renders nothing while the allowance has room, so it
 * can sit unconditionally in a layout.
 *
 * @param {object} props
 * @param {string} props.resource
 * @param {'panel'|'inline'|'banner'} [props.variant]
 */
export function CapacityNotice({ resource, variant = 'inline', className = '' }) {
  const { body, isReady } = useEntitlements();
  if (!isReady) return null;
  const refusal = body?.limit_refusals?.[resource] ?? null;
  if (!refusal) return null;
  return <UpgradePrompt refusal={refusal} variant={variant} className={className} />;
}

/**
 * Whether the account may do something, as a boolean, for a call site that needs to branch rather
 * than wrap.
 *
 * Returns `{ permitted, refusal, isReady }`. `permitted` is `false` until entitlements are known,
 * for the reason the docblock gives: a control that appears enabled before its plan is known is a
 * control that 403s.
 *
 * @param {string} feature
 */
export function useFeature(feature) {
  const { body, isReady } = useEntitlements();
  return {
    permitted: isReady && hasFeature(body, feature),
    refusal: lockedFeatureRefusal(body, feature),
    isReady,
  };
}

/**
 * Whether the account has room in an allowance.
 *
 * Returns `{ hasRoom, entry, refusal, isReady }`. `hasRoom` is `false` while entitlements are
 * unknown AND while the usage figure is unreadable — an action whose limit cannot be checked is not
 * an action to encourage, and the backend will refuse it with a 503 anyway
 * (`ENTITLEMENT_USAGE_UNREADABLE`) rather than guessing.
 *
 * @param {string} resource
 */
export function useAllowance(resource) {
  const { body, isReady } = useEntitlements();
  if (!isReady) {
    return { hasRoom: false, entry: null, refusal: null, isReady: false };
  }
  const entry = allowance(body, resource);
  const measurable = entry.used.available && (entry.isCustom || entry.limit.available);
  return {
    hasRoom: entry.isCustom ? true : measurable && !entry.exhausted,
    entry,
    refusal: body?.limit_refusals?.[resource] ?? null,
    isReady: true,
  };
}

export { hasText };
export default FeatureGate;
