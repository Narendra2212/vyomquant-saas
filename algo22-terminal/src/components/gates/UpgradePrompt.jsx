/**
 * ═══════════════════════════════════════════════════════════════════════════
 * UpgradePrompt — the one way this app says "your plan does not include this"
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * EVERY SENTENCE HERE COMES FROM THE SERVER
 * ----------------------------------------
 * The headline, the explanation and the button label are `message`, `upgrade_message` and
 * `cta_label` off the backend's structured refusal
 * (`core/subscription_dependencies.py::EntitlementRefusal`). This component chooses the layout and
 * nothing else.
 *
 * That is deliberate and it is the difference between this and the usual upgrade panel. If the copy
 * were composed here it would need a local plan ladder and a local price list to compose it from,
 * and those would be a second source of truth for both — free to drift from the catalogue the
 * backend actually enforces and the gateway actually charges. A trader would then be told "upgrade
 * to Pro for ₹999" by a bundle that was built before the price changed. Rendering the server's own
 * words makes that impossible: there is one price list, and it is the one that takes the payment.
 *
 * NO FAKE SCARCITY, AND NO INVENTED NUMBERS
 * -----------------------------------------
 * `current` and `limit` are rendered only when the server supplied both. There is no countdown, no
 * "only N left", and no "you've used 80% of…" computed from a figure that was not measured. When the
 * backend could not establish a usage figure it says so, and this renders the reason instead of a
 * number — the rule `design/reported.js` exists to enforce.
 *
 * WHERE IT IS SHOWN
 * -----------------
 * Three shapes, one component:
 *
 *   `variant="panel"`  the full locked state, standing in for a feature's own UI.
 *   `variant="inline"` a single line under a disabled control.
 *   `variant="banner"` the top-of-page notice, for an over-capacity account after a downgrade.
 */

import React from 'react';
import { Lock, Sparkles, TrendingUp } from 'lucide-react';
import { Link } from 'react-router-dom';

import { CommandButton } from '../ds/CommandButton';

/** Where a self-serve upgrade goes. The authenticated plan catalogue, not the marketing page. */
export const UPGRADE_ROUTE = '/app/billing';

/**
 * Where a sales conversation goes.
 *
 * A `mailto:` rather than a `/contact` route, because this application has none and `App.jsx`'s
 * catch-all redirects any unknown path to `/` — so a "Talk to Sales" button pointing at a route
 * that does not exist would read as working and silently drop the trader on the landing page. This
 * is the address already published as the commercial contact in `components/legal/LegalPage.jsx`.
 */
export const SALES_ROUTE = 'mailto:billing@vyomquant.com?subject=VyomQuant%20Enterprise%20enquiry';

/** Whether a destination leaves the router and therefore needs an anchor, not a `Link`. */
const isExternal = (destination) =>
  typeof destination === 'string' && /^(mailto:|https?:)/.test(destination);

/**
 * The call to action, rendered with the right element for its destination.
 *
 * `react-router`'s `Link` composes its `to` against the current path, so a `mailto:` handed to it
 * becomes a navigation to a nonexistent route rather than opening a mail client. One component
 * decides this so no call site has to remember which kind of destination it has.
 */
function CtaLink({ destination, className, children }) {
  if (isExternal(destination)) {
    return (
      <a href={destination} className={className}>
        {children}
      </a>
    );
  }
  return (
    <Link to={destination} className={className}>
      {children}
    </Link>
  );
}

const VARIANTS = Object.freeze(['panel', 'inline', 'banner']);

/** True for a string with visible content. Same test the `ds/` primitives apply. */
const hasText = (value) => typeof value === 'string' && value.trim() !== '';

/**
 * The destination for a refusal's call to action.
 *
 * A refusal naming a `required_plan` is self-serve, so it goes to the billing page where that plan
 * can be bought. A refusal with `contact_sales` has no plan to name — it is the top of the ladder —
 * so it goes to the pricing section's enterprise block. Those are the server's two shapes and this
 * is the only place that maps them to routes.
 */
export function upgradeDestination(refusal) {
  return refusal?.contact_sales === true ? SALES_ROUTE : UPGRADE_ROUTE;
}

/**
 * @param {object} props
 * @param {object|null} props.refusal The server's refusal payload. Required — without it there is
 *   nothing honest to render, and the component returns `null` rather than inventing a prompt.
 * @param {'panel'|'inline'|'banner'} [props.variant]
 * @param {string} [props.title] Overrides the refusal's own `message`. For the rare surface that
 *   needs to name the feature rather than the limit ("ML Strategy Nodes"), never to restate a price.
 * @param {React.ReactNode} [props.children] Extra context rendered under the server's sentences.
 * @param {string} [props.className]
 */
export function UpgradePrompt({
  refusal,
  variant = 'panel',
  title,
  children,
  className = '',
}) {
  // No refusal, no prompt. A gate that cannot say WHY something is locked should render nothing
  // and leave the surface alone; a bare "Upgrade" with no reason is an advertisement, not an
  // explanation, and it is what this component exists instead of.
  if (!refusal || typeof refusal !== 'object') return null;

  const resolvedVariant = VARIANTS.includes(variant) ? variant : 'panel';
  const headline = hasText(title) ? title : refusal.message;
  const explanation = refusal.upgrade_message;
  const ctaLabel = hasText(refusal.cta_label) ? refusal.cta_label : null;
  const destination = upgradeDestination(refusal);

  // Rendered only when the server measured both sides. `limit === 0` is "not included in this
  // plan" rather than "you are at your limit", and the server's `message` already says that — so
  // the figures would add nothing but a confusing `0 / 0`.
  const showsUsage =
    Number.isFinite(Number(refusal.current))
    && Number.isFinite(Number(refusal.limit))
    && Number(refusal.limit) > 0;

  const Glyph = refusal.contact_sales === true ? TrendingUp : Lock;

  if (resolvedVariant === 'inline') {
    return (
      <p
        data-gate="upgrade-prompt"
        data-gate-variant="inline"
        data-gate-code={refusal.code}
        className={`flex flex-wrap items-center gap-x-2 gap-y-1 text-body text-content-secondary ${className}`.trim()}
      >
        <Lock size={12} aria-hidden="true" className="shrink-0 text-content-muted" />
        {hasText(headline) ? <span>{headline}</span> : null}
        {ctaLabel ? (
          <CtaLink
            destination={destination}
            className="font-semibold text-brand underline decoration-brand/40 underline-offset-2 hover:decoration-brand"
          >
            {ctaLabel}
          </CtaLink>
        ) : null}
      </p>
    );
  }

  if (resolvedVariant === 'banner') {
    return (
      <div
        data-gate="upgrade-prompt"
        data-gate-variant="banner"
        data-gate-code={refusal.code}
        className={`flex flex-col gap-3 rounded-lg border border-status-warning/30 bg-status-warning-wash p-4 sm:flex-row sm:items-center sm:justify-between ${className}`.trim()}
      >
        <div className="flex min-w-0 items-start gap-2.5">
          <Glyph size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-status-warning" />
          <div className="min-w-0">
            {hasText(headline) ? (
              <p className="text-title font-semibold text-content-primary">{headline}</p>
            ) : null}
            {hasText(explanation) ? (
              <p className="mt-1 text-body text-content-secondary">{explanation}</p>
            ) : null}
            {children}
          </div>
        </div>
        {ctaLabel ? (
          <CtaLink
            destination={destination}
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-md bg-brand px-4 py-2 text-body font-semibold text-content-inverse transition-colors hover:bg-brand-hover"
          >
            {ctaLabel}
          </CtaLink>
        ) : null}
      </div>
    );
  }

  return (
    <div
      data-gate="upgrade-prompt"
      data-gate-variant="panel"
      data-gate-code={refusal.code}
      className={`flex flex-col items-center gap-3 rounded-lg border border-line-default bg-surface-raised px-6 py-8 text-center ${className}`.trim()}
    >
      <span className="flex h-9 w-9 items-center justify-center rounded-full bg-brand-wash">
        <Glyph size={16} aria-hidden="true" className="text-brand" />
      </span>

      {hasText(headline) ? (
        <h3 className="text-section font-bold text-content-primary">{headline}</h3>
      ) : null}

      {hasText(explanation) ? (
        <p className="max-w-md text-body text-content-secondary">{explanation}</p>
      ) : null}

      {showsUsage ? (
        // Monospace and tabular because it is a FIGURE, which is Requirement 3.1's rule for an
        // identifier or a measurement rather than prose.
        <p className="font-mono text-small tabular-nums text-content-muted">
          {Number(refusal.current)} / {Number(refusal.limit)} used
        </p>
      ) : null}

      {children}

      {ctaLabel ? (
        <CtaLink
          destination={destination}
          className="mt-1 inline-flex items-center justify-center gap-2 rounded-md bg-brand px-5 py-2.5 text-body font-semibold text-content-inverse transition-colors hover:bg-brand-hover"
        >
          <Sparkles size={14} aria-hidden="true" />
          {ctaLabel}
        </CtaLink>
      ) : null}
    </div>
  );
}

/**
 * A control that is present but refused, with the server's reason attached.
 *
 * Preferred over hiding the control outright wherever the capability is one a trader would look
 * for: a missing button is indistinguishable from a bug, while a disabled one carrying
 * `disabledReason` answers "why can't I do this?" without a support ticket. `ds/CommandButton`
 * renders that reason as visible text rather than a `title`, because a disabled button cannot be
 * hovered or focused.
 *
 * @param {object} props
 * @param {object|null} props.refusal
 * @param {React.ReactNode} props.children The label the control would have had.
 * @param {string} [props.className]
 */
export function LockedCommand({ refusal, children, className = '' }) {
  const reason = [refusal?.message, refusal?.upgrade_message].filter(hasText).join(' ');
  return (
    <CommandButton
      intent="secondary"
      icon={Lock}
      disabled
      disabledReason={hasText(reason) ? reason : 'Your plan does not include this.'}
      className={className}
      data-gate="locked-command"
      data-gate-code={refusal?.code}
    >
      {children}
    </CommandButton>
  );
}

export default UpgradePrompt;
