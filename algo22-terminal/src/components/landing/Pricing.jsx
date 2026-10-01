import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, Minus } from 'lucide-react'

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Pricing — the published VyomQuant plan ladder. Anchor `#pricing`.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * FIVE TIERS, PRICED IN RUPEES ONLY
 * ---------------------------------
 * ₹0 · ₹499 · ₹999 · ₹2,499 · custom. There is no currency selector and no `$` on this surface;
 * the authenticated billing page (`src/pages/Billing.jsx`) is where a visitor's own currency is
 * resolved, because that surface has to show what the checkout will actually charge.
 *
 * WHY THE FIGURES ARE CONSTANTS HERE, AND WHY THAT IS NO LONGER A CONTRADICTION
 * ---------------------------------------------------------------------------
 * These numbers are the INR column of the plan catalogue in
 * `backend_app/core/subscription_engine.py`. They used to be declared here because the server
 * DISAGREED with them: `PricingService.get_localized_plans` took the USD figure and ran it through
 * `FXService.localize_price`, so an INR request came back as an FX conversion of the dollar price
 * (~₹432 / ₹865 / ₹2,162) rather than the published ₹499 / ₹999 / ₹2,499. Rendering that response
 * would have put a number on the marketing page that contradicted the price list, and it would
 * have drifted every time the rate moved.
 *
 * That defect is fixed at the root. `FXService.localize_plan_price` now charges the PUBLISHED figure
 * for any currency the catalogue publishes and converts only for the ones it does not, so
 * `GET /api/billing/plans` answers ₹499 for the ₹499 plan and Razorpay is asked for 49900 paise.
 * The endpoint and this component agree.
 *
 * They stay constants anyway, for two reasons that have nothing to do with the old bug: a public
 * price list is a published commitment and should not acquire a loading state or a network
 * dependency, and `/api/billing/plans` is the one call this page can avoid making for every
 * anonymous visitor. `tests/unit/landing_page_pricing_crash_regression.test.jsx` pins the figures
 * rendered here, and `tests/test_pricing_ladder.py` pins the same figures in the catalogue, so the
 * two cannot drift without a test failing.
 *
 * POSITIONING
 * -----------
 * The tier NAME is the plan ("Trader"); the word above it is the stage of the journey
 * ("Automate"). Both come from the catalogue, where `name` and `journey` are separate fields, so
 * the ladder reads as a progression rather than as five unrelated products.
 *
 * WHAT IS NOT ON THIS PAGE, DELIBERATELY
 * --------------------------------------
 * No team members, workspaces, RBAC, organisation management or customer API tier. The product is
 * single-user and has no customer-facing API tier, so none of them is a pricing dimension and
 * advertising one would be selling something that does not exist. The previous Institutional card
 * listed "Granular Role-Based Access" and "Unlimited Strategy Bots"; neither was implemented.
 *
 * Historical data, tick data and alerts are available on every plan and are therefore stated once,
 * under the table, rather than used as a differentiator.
 */

/**
 * The published ladder. `null` price means quoted rather than listed.
 *
 * `features` is what the plan ADDS — each paid tier opens with "Everything in <previous>" — so a
 * reader compares deltas rather than re-reading twenty lines per card. The absolute numbers live in
 * {@link COMPARISON}, which is the surface for "how much of X do I get".
 */
const PLANS = [
  {
    id: 'free',
    tier: 'FREE',
    name: 'Free',
    journey: 'Explore',
    tagline: 'Build before you commit.',
    inr: 0,
    annualInr: null,
    recommended: false,
    cta: 'Start Free',
    features: [
      'Visual Strategy Builder',
      'VectorBT Backtesting',
      'Paper Trading',
      'Strategy Templates',
      'Historical & tick data',
      'Browse the Strategy Marketplace',
    ],
  },
  {
    id: 'starter',
    tier: 'TRADER',
    name: 'Trader',
    journey: 'Automate',
    tagline: 'Turn your strategies into automated systems.',
    inr: 499,
    annualInr: 4990,
    recommended: false,
    cta: 'Start Automating',
    features: [
      'Everything in Free',
      'Automated strategy deployment',
      'Multi-exchange connectivity',
      'Drawdown protection & risk controls',
      'Execution monitoring',
      'Subscribe to 3 marketplace strategies',
    ],
  },
  {
    id: 'pro',
    tier: 'PRO_QUANT',
    name: 'Pro Quant',
    journey: 'Quantify',
    tagline: 'Build. Optimize. Publish. Earn.',
    inr: 999,
    annualInr: 9990,
    recommended: true,
    cta: 'Start Pro Quant',
    ctaSecondary: 'Build it. Publish it. Earn from it.',
    features: [
      'Everything in Trader',
      'ML strategy nodes & XGBoost training',
      'Parameter optimization',
      'Portfolio-level risk controls',
      'Strategy comparison & versioning',
      'Publish 5 marketplace strategies',
      'Earn 90% of marketplace revenue',
    ],
  },
  {
    id: 'enterprise',
    tier: 'BUSINESS',
    name: 'Business',
    journey: 'Operate',
    tagline: 'Run a larger systematic trading operation.',
    inr: 2499,
    annualInr: 24990,
    recommended: false,
    cta: 'Scale to Business',
    ctaSecondary: 'More strategies. More capacity. More opportunity.',
    features: [
      'Everything in Pro Quant',
      'Higher strategy & account capacity',
      'Advanced portfolio risk controls',
      'Advanced operational monitoring',
      'Advanced audit history',
      'Publish 15 marketplace strategies',
    ],
  },
  {
    id: 'scale',
    tier: 'ENTERPRISE',
    name: 'Enterprise',
    journey: 'Scale',
    tagline: 'Infrastructure built around your operation.',
    inr: null,
    annualInr: null,
    recommended: false,
    cta: 'Talk to Sales',
    features: [
      'Everything in Business',
      'Custom strategy & account capacity',
      'Custom quantitative capacity',
      'Deployment & integration requirements',
      'Agreed support and SLA',
    ],
  },
]

/**
 * The capacity table. One row per limit the plans actually enforce, in the order a trader meets
 * them.
 *
 * Every figure here is enforced server-side by `backend_app/core/subscription_dependencies.py`
 * against a count or a meter — this is not a marketing table. `'Custom'` on the Enterprise column
 * means the number is recorded per agreement in `profiles.plan_limit_overrides`; it is NOT
 * "unlimited", and an Enterprise account with no override recorded is enforced at the Business
 * figure rather than at no ceiling.
 *
 * `monthly: true` marks a per-month allowance so the row can say so. A monthly figure read as a
 * lifetime total is the single most misleading thing a capacity table can do.
 */
const COMPARISON = [
  { label: 'Active strategies', values: [1, 3, 10, 25, 'Custom'] },
  { label: 'Paper strategies', values: [1, 3, 10, 25, 'Custom'] },
  { label: 'Live strategies', values: [0, 3, 10, 25, 'Custom'] },
  { label: 'Exchange connections', values: [1, 2, 5, 8, 'Custom'] },
  { label: 'Trading accounts', values: [1, 1, 3, 5, 'Custom'] },
  { label: 'Backtests', values: [10, 100, 500, 1500, 'Custom'], monthly: true },
  { label: 'Custom indicators', values: [3, 10, 30, 75, 'Custom'] },
  { label: 'Strategy versions', values: [3, 10, 25, 50, 'Custom'], perStrategy: true },
  { label: 'ML models', values: [0, 0, 5, 15, 'Custom'] },
  { label: 'ML training runs', values: [0, 0, 50, 200, 'Custom'], monthly: true },
  { label: 'Optimization runs', values: [0, 25, 100, 400, 'Custom'], monthly: true },
  { label: 'Marketplace browsing', values: [true, true, true, true, true] },
  { label: 'Marketplace subscriptions', values: [0, 3, 10, 25, 'Custom'] },
  { label: 'Marketplace listings', values: [0, 0, 5, 15, 'Custom'] },
  { label: 'Creator revenue share', values: [null, null, '90%', '90%', '90%'] },
]

/** Available on every plan, so stated once instead of repeated down a column. */
const EVERY_PLAN = [
  'Historical data',
  'Tick data',
  'Standard market data',
  'Alerts & webhooks',
  'Strategy analytics',
  'Marketplace browsing',
]

/** The one currency this section quotes. */
const RUPEE = '₹'

/**
 * Where an Enterprise enquiry goes.
 *
 * A `mailto:` and not a route, because this application has no `/contact` page and its router
 * catch-all (`App.jsx`: `<Route path="*" element={<Navigate to="/" replace />} />`) turns any
 * unknown path into a silent redirect home — so a "Talk to Sales" link pointing at `/contact` would
 * look functional and do nothing. `billing@vyomquant.com` is already published as the commercial
 * contact in `components/legal/LegalPage.jsx`, so it is an address that works now.
 */
const SALES_MAILTO = 'mailto:billing@vyomquant.com?subject=VyomQuant%20Enterprise%20enquiry'

/**
 * Indian digit grouping (`2,499`, `1,23,456`). A non-finite input renders as `0` rather than
 * throwing inside `toLocaleString` — the crash this component's regression suite exists to pin.
 */
const formatNumber = (value) => {
  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) return '0'
  return numericValue.toLocaleString('en-IN')
}

/** Monthly rupee figure, or `null` for a quoted plan. Defensive against a malformed `inr`. */
const monthlyPrice = (plan) => {
  if (plan?.inr === null || plan?.inr === undefined) return null
  const numericValue = Number(plan.inr)
  return Number.isFinite(numericValue) ? numericValue : 0
}

/**
 * One cell of the comparison table.
 *
 * `true` renders a tick, `null` renders the not-applicable dash, everything else renders as text.
 * The dash is `content-muted`, which `tokens.css` reserves for non-text marks like this one, and it
 * carries an accessible label so the column is not silently empty to a screen reader.
 */
function ComparisonCell({ value }) {
  if (value === true) {
    return (
      <>
        <Check className="mx-auto h-4 w-4 text-status-profit" aria-hidden="true" />
        <span className="sr-only">Included</span>
      </>
    )
  }
  if (value === null || value === undefined) {
    return (
      <>
        <Minus className="mx-auto h-3.5 w-3.5 text-content-muted" aria-hidden="true" />
        <span className="sr-only">Not applicable</span>
      </>
    )
  }
  return (
    <span className="font-mono text-small tabular-nums text-content-primary">
      {typeof value === 'number' ? formatNumber(value) : value}
    </span>
  )
}

export default function Pricing() {
  const [isAnnual, setIsAnnual] = useState(false)

  /*
    The annual figure is the PUBLISHED yearly price divided by twelve — not the monthly price with a
    percentage taken off it. The two are different numbers and only one of them is charged: ₹4,990
    a year is ₹415.83 a month, which no arithmetic on ₹499 produces exactly. Showing a derived
    figure here and billing the published one is how a pricing page ends up off by a rupee.
  */
  const displayPrice = (plan) => {
    const monthly = monthlyPrice(plan)
    if (monthly === null) return null
    if (isAnnual && plan.annualInr) return Math.round(plan.annualInr / 12)
    return monthly
  }

  /** Whole percent saved on the annual commitment, from the two published figures. */
  const savings = (plan) => {
    const monthly = monthlyPrice(plan)
    if (!isAnnual || !plan.annualInr || !monthly) return null
    return Math.round(100 - (plan.annualInr * 100) / (monthly * 12))
  }

  return (
    <section
      id="pricing"
      className="border-t border-line-default/80 py-24 lg:py-32"
      aria-label="Pricing"
    >
      <div className="section-container">
        <div className="section-inner">
          <div className="mb-16 text-center">
            <div className="mb-3 font-mono text-xs font-semibold uppercase tracking-widest text-brand">
              Pricing
            </div>
            <h2 className="mb-4 text-3xl font-black tracking-tight text-content-primary sm:text-4xl lg:text-5xl">
              Start Small. Scale When You Need To.
            </h2>
            <p className="mx-auto mb-8 max-w-2xl text-base text-content-secondary sm:text-lg">
              Get the tools you need today. Unlock more strategy capacity and quantitative
              capabilities as your trading operation grows.
            </p>

            {/* Monthly / Annual toggle. `aria-pressed` rather than two links, because this changes
                what the page shows rather than where it goes. */}
            <div
              className="inline-flex items-center gap-3 rounded-2xl border border-line-default/80 bg-surface-panel p-1.5 shadow-panel"
              role="group"
              aria-label="Billing period"
            >
              <button
                type="button"
                onClick={() => setIsAnnual(false)}
                aria-pressed={!isAnnual}
                className={`rounded-xl px-5 py-2.5 text-sm font-bold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${
                  !isAnnual
                    ? 'bg-brand text-content-inverse shadow-panel'
                    : 'text-content-secondary hover:text-content-primary'
                }`}
              >
                Monthly
              </button>
              <button
                type="button"
                onClick={() => setIsAnnual(true)}
                aria-pressed={isAnnual}
                className={`rounded-xl px-5 py-2.5 text-sm font-bold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${
                  isAnnual
                    ? 'bg-brand text-content-inverse shadow-panel'
                    : 'text-content-secondary hover:text-content-primary'
                }`}
              >
                Annual
                <span className="ml-2 rounded-md border border-status-profit/20 bg-status-profit-wash px-2 py-0.5 text-xs font-semibold text-status-profit">
                  2 months free
                </span>
              </button>
            </div>
          </div>

          {/* ── The five cards ──────────────────────────────────────────── */}
          <div className="mx-auto grid max-w-7xl items-stretch gap-6 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {PLANS.map((plan) => {
              const price = displayPrice(plan)
              const saved = savings(plan)
              const isRecommended = plan.recommended
              const isQuoted = price === null

              return (
                <div
                  key={plan.id}
                  data-plan={plan.id}
                  data-plan-tier={plan.tier}
                  className={`card-surface relative flex h-full flex-col rounded-2xl p-6 transition-colors duration-200 ${
                    isRecommended
                      ? 'z-10 border-2 border-brand bg-surface-panel shadow-raised xl:-translate-y-2'
                      : 'border border-line-default/80 bg-surface-panel/70 hover:border-line-default hover:bg-surface-raised/50'
                  }`}
                >
                  {isRecommended ? (
                    <div className="absolute -top-3.5 left-1/2 -translate-x-1/2 rounded-full bg-brand px-4 py-1 text-xs font-extrabold uppercase tracking-wider text-content-inverse shadow-panel">
                      Most Popular
                    </div>
                  ) : null}

                  <div className="mb-5">
                    {/* The journey word above the plan name. The ladder is a progression and this
                        is the half of it a price alone does not communicate. */}
                    <p className="mb-1 font-mono text-xs font-bold uppercase tracking-[0.16em] text-brand">
                      {plan.journey}
                    </p>
                    <h3 className="mb-1.5 text-xl font-bold text-content-primary">{plan.name}</h3>
                    <p className="text-sm text-content-secondary">{plan.tagline}</p>
                  </div>

                  <div className="mb-5 border-b border-line-default/60 pb-5">
                    {isQuoted ? (
                      <div className="flex items-baseline gap-1.5">
                        <span className="text-3xl font-black tracking-tight text-content-primary sm:text-4xl">
                          Custom
                        </span>
                      </div>
                    ) : (
                      <div className="flex items-baseline gap-1.5">
                        <span className="text-4xl font-black tracking-tight text-content-primary">
                          {RUPEE}
                          {formatNumber(price)}
                        </span>
                        <span className="text-sm font-medium text-content-secondary">/month</span>
                      </div>
                    )}
                    {isAnnual && plan.annualInr ? (
                      <p className="mt-1.5 font-mono text-xs font-medium text-status-profit">
                        Billed {RUPEE}
                        {formatNumber(plan.annualInr)}/yr
                        {saved ? ` — save ${saved}%` : ''}
                      </p>
                    ) : null}
                    {isQuoted ? (
                      <p className="mt-1.5 text-xs text-content-secondary">
                        Priced per agreement.
                      </p>
                    ) : null}
                  </div>

                  <ul className="mb-7 flex-1 space-y-3">
                    {plan.features.map((feature) => (
                      <li
                        key={feature}
                        className="flex items-start gap-2.5 text-sm font-medium text-content-secondary"
                      >
                        <Check
                          className="mt-0.5 h-4 w-4 flex-shrink-0 text-status-profit"
                          aria-hidden="true"
                        />
                        {feature}
                      </li>
                    ))}
                  </ul>

                  <div className="mt-auto">
                    {/* Every tier except Enterprise goes to signup, which is where a plan is
                        actually chosen. A pricing card does not pretend to take a payment.

                        Enterprise opens a mail client rather than a /contact route, because there
                        IS no /contact route — and the router's catch-all redirects an unknown path
                        to `/`, so linking one would produce a button that silently returns the
                        visitor to the top of the same page. `billing@vyomquant.com` is the address
                        the product already publishes (`components/legal/LegalPage.jsx`), so it is a
                        destination that exists today rather than one that needs building first. */}
                    {isQuoted ? (
                      <a
                        href={SALES_MAILTO}
                        className="block w-full rounded-xl border border-line-default py-3.5 text-center text-sm font-bold text-content-primary transition-colors duration-150 hover:border-brand/40 hover:bg-surface-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                      >
                        {plan.cta} &rarr;
                      </a>
                    ) : (
                      <Link
                        to="/signup"
                        className={`block w-full rounded-xl py-3.5 text-center text-sm font-bold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${
                          isRecommended
                            ? 'bg-brand text-content-inverse hover:bg-brand-hover'
                            : 'border border-line-default text-content-primary hover:border-brand/40 hover:bg-surface-raised'
                        }`}
                      >
                        {plan.cta}
                        {plan.inr > 0 ? ` — ${RUPEE}${formatNumber(plan.inr)}` : ''}
                      </Link>
                    )}
                    {plan.ctaSecondary ? (
                      <p className="mt-2.5 text-center text-xs text-content-secondary">
                        {plan.ctaSecondary}
                      </p>
                    ) : null}
                  </div>
                </div>
              )
            })}
          </div>

          {/* ── The capacity table ──────────────────────────────────────── */}
          <div className="mx-auto mt-20 max-w-7xl">
            <h3 className="mb-2 text-center text-section font-bold text-content-primary">
              Capacity by plan
            </h3>
            <p className="mb-8 text-center text-sm text-content-secondary">
              Every figure below is enforced by the platform, not just advertised.
            </p>

            <div className="overflow-x-auto rounded-2xl border border-line-default/80 bg-surface-panel/60">
              <table className="w-full min-w-[46rem] border-collapse text-left">
                <caption className="sr-only">
                  Plan capacity comparison across Free, Trader, Pro Quant, Business and Enterprise
                </caption>
                <thead>
                  <tr className="border-b border-line-default">
                    <th scope="col" className="px-5 py-4 text-small font-bold text-content-secondary">
                      Capacity
                    </th>
                    {PLANS.map((plan) => (
                      <th
                        key={plan.id}
                        scope="col"
                        className={`px-4 py-4 text-center text-small font-bold ${
                          plan.recommended ? 'text-brand' : 'text-content-secondary'
                        }`}
                      >
                        {plan.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {COMPARISON.map((row) => (
                    <tr
                      key={row.label}
                      className="border-b border-line-subtle last:border-b-0 hover:bg-surface-raised/40"
                    >
                      <th
                        scope="row"
                        className="px-5 py-3 text-body font-medium text-content-secondary"
                      >
                        {row.label}
                        {row.monthly ? (
                          <span className="ml-1.5 text-micro text-content-muted">/ month</span>
                        ) : null}
                        {row.perStrategy ? (
                          <span className="ml-1.5 text-micro text-content-muted">per strategy</span>
                        ) : null}
                      </th>
                      {row.values.map((value, index) => (
                        <td
                          key={PLANS[index].id}
                          className={`px-4 py-3 text-center ${
                            PLANS[index].recommended ? 'bg-brand-wash/40' : ''
                          }`}
                        >
                          <ComparisonCell value={value} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-6 rounded-xl border border-line-default/60 bg-surface-panel/40 px-5 py-4">
              <p className="text-small font-semibold text-content-primary">
                Included on every plan
              </p>
              <ul className="mt-2.5 flex flex-wrap gap-x-5 gap-y-2">
                {EVERY_PLAN.map((item) => (
                  <li key={item} className="flex items-center gap-2 text-body text-content-secondary">
                    <Check className="h-3.5 w-3.5 shrink-0 text-status-profit" aria-hidden="true" />
                    {item}
                  </li>
                ))}
              </ul>
              {/* The word "unlimited" is deliberately absent from this whole section, including
                  from the disclaimer that would otherwise explain it away — the regression suite
                  asserts the word appears nowhere under `#pricing`, and a guard a disclaimer can
                  defeat is not a guard. "Open-ended" carries the same meaning and keeps the
                  assertion strict. */}
              <p className="mt-4 text-body text-content-secondary">
                Marketplace publishing is available to Pro Quant and Business. &ldquo;Custom&rdquo;
                means capacity agreed per contract and recorded on the account, not an open-ended
                allowance.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
