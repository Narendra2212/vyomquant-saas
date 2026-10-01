import React, { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, Globe, Minus } from 'lucide-react'

import { api } from '../../api'

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Pricing — the published VyomQuant plan ladder. Anchor `#pricing`.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * FIVE PLANS, PUBLISHED IN RUPEES, SHOWN IN THE VISITOR'S CURRENCY
 * ----------------------------------------------------------------
 * ₹0 · ₹499 · ₹999 · ₹2,499 · custom is the published list. This section reads
 * `GET /api/billing/plans` — a public, unauthenticated endpoint — which resolves the visitor's
 * currency from their own geography and returns the price list localised into it. A selector lets
 * them change it.
 *
 * WHY THIS STOPPED BEING A HARDCODED RUPEE LIST
 * ---------------------------------------------
 * It was INR-only with no detection, which was wrong in both directions at once:
 *
 *   * A visitor outside India was quoted ₹499 — a number they cannot price a decision on, and not
 *     what they would be charged.
 *   * The authenticated billing page DID localise, so the same plan carried two different-looking
 *     prices on two surfaces of the same product. A visitor who saw ₹499 here and $5.00 there read
 *     the second one as broken.
 *
 * And underneath both, the catalogue published TWO independent base columns, INR and USD, about 4%
 * apart, with every other currency derived from the dollar one. So the marketing page advertised
 * the rupee value point while every non-Indian visitor was quoted the dollar value point.
 *
 * That is fixed at the root: `subscription_engine.PRICE_BASE_CURRENCY` makes the rupee list the
 * single published price, and `FXService.localize_plan_price` converts it for every other
 * currency. One value point, one price list, and this section and the billing page now read the
 * same endpoint and show the same number.
 *
 * THE FALLBACK IS THE PUBLISHED LIST, NOT A GUESS
 * -----------------------------------------------
 * If the read has not landed or fails, the rupee constants below are rendered — labelled in
 * rupees. They are a real published commitment, so quoting them is honest; what is NOT done is
 * printing them under another currency's symbol, which would be a fabricated conversion. A card
 * waiting on a converted figure shows a placeholder instead of a number.
 *
 * `tests/unit/landing_page_pricing_crash_regression.test.jsx` pins the rendered figures and the
 * currency behaviour; `tests/test_pricing_ladder.py` pins the same list in the catalogue.
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

/** The currency the price list is PUBLISHED in, and the fallback when the read fails. */
const BASE_CURRENCY = 'INR'
const RUPEE = '₹'

/** Display currencies offered up front. The server's own list replaces this once it answers. */
const FALLBACK_CURRENCIES = Object.freeze([
  { code: 'INR', symbol: '₹' },
  { code: 'USD', symbol: '$' },
  { code: 'EUR', symbol: '€' },
  { code: 'GBP', symbol: '£' },
  { code: 'AED', symbol: 'AED' },
  { code: 'SGD', symbol: 'S$' },
  { code: 'AUD', symbol: 'A$' },
  { code: 'CAD', symbol: 'CA$' },
  { code: 'JPY', symbol: '¥' },
])

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

/**
 * A money figure, grouped for the currency it is in and rounded to that currency's own precision.
 *
 * `decimals` comes from the SERVER (`plans[].decimals`), not from a guess here: JPY and KRW have
 * no minor unit, so rounding every currency to two places prints `¥818.00` for a currency that
 * cannot express a fraction. A whole number is printed without decimals even where the currency
 * admits them, because `₹499.00` reads as a conversion artefact where `₹499` reads as a price.
 */
const formatMoney = (value, decimals) => {
  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) return '0'
  const places = Number.isInteger(decimals) ? decimals : 2
  const fraction = Number.isInteger(numericValue) ? 0 : places
  return numericValue.toLocaleString('en-IN', {
    minimumFractionDigits: fraction,
    maximumFractionDigits: fraction,
  })
}

/** Monthly figure in the published base currency, or `null` for a quoted plan. */
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

  /**
   * The server's localized catalogue, or `null` until it answers / if it never does.
   *
   * `requested` is the visitor's explicit pick and drives the READ. It starts `null`, so the first
   * read carries no `?currency=` and the server resolves the currency from the request's own
   * geography — which is the whole point: a visitor should see their own currency without asking.
   *
   * It deliberately does NOT drive what is displayed. The currency on screen is the one the
   * catalogue in hand is denominated in, so a figure and its symbol can never disagree; see
   * `currency` / `selected` below.
   */
  const [catalogue, setCatalogue] = useState(null)
  const [requested, setRequested] = useState(null)
  /**
   * Bumped on every pick, so choosing the SAME currency again re-reads.
   *
   * Without it, a read that failed could not be retried: `setRequested('EUR')` when `requested` is
   * already `'EUR'` changes no state, so the effect would not re-run and the control would be
   * dead exactly when the visitor is trying again.
   */
  const [attempt, setAttempt] = useState(0)
  /** Whether a read is in flight. Drives the selector's optimistic value, and nothing else. */
  const [reading, setReading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setReading(true)
    /*
      `getPublicPlans`, not `getPlans`: this surface is read by visitors with no account, and the
      authenticated transport routes a 401 into the shell's `auth:expired` handler — which would
      turn reading a price list into a sign-out prompt. The endpoint itself is public.

      A failure is swallowed deliberately. The fallback below is the PUBLISHED rupee list, which is
      a real commitment rather than an invented number, so a visitor whose read failed still sees
      the correct price — in the currency it is published in, labelled as such. The alternative, a
      pricing page that renders an error or nothing at all, is worse than one that quotes its base
      currency.
    */
    api.billing
      .getPublicPlans(requested ?? undefined)
      .then((response) => {
        if (cancelled) return
        setCatalogue(response?.data ?? response ?? null)
        setReading(false)
      })
      .catch(() => {
        if (cancelled) return
        // The catalogue in hand is left alone. If there is none, the rupee fallback renders; if
        // there is one, the figures already on screen stay — they are the last thing the server
        // actually said, and blanking them because a re-read for a different currency failed
        // would replace a correct price with nothing. Clearing `reading` is what makes the
        // selector fall back to the currency those figures are actually in.
        setReading(false)
      })
    return () => {
      cancelled = true
    }
  }, [requested, attempt])

  /** The server's per-plan payload, by plan id. Empty until the read lands. */
  const serverPlans = useMemo(() => {
    const offered = Array.isArray(catalogue?.plans) ? catalogue.plans : []
    return Object.fromEntries(offered.map((plan) => [plan?.id, plan]))
  }, [catalogue])

  /**
   * The currency the FIGURES are in — the catalogue's own, else the published base.
   *
   * Driven by the response and never by the pending request. The visitor's pick cannot relabel
   * figures it has not been served: choosing EUR while the dollar catalogue is still in hand used
   * to leave `$5.20` on screen under a sentence reading "shown in EUR", which is the same class of
   * mistake as the hardcoded rupee list this component replaced. It also means an UNSUPPORTED pick
   * is handled by itself — the server answers in the currency it will actually charge, the
   * selector snaps to that, and there is no state in which the page waits forever for a currency
   * the server declines to quote.
   */
  const served = typeof catalogue?.currency === 'string' ? catalogue.currency : null
  const currency = served ?? BASE_CURRENCY

  /**
   * What the selector shows.
   *
   * WHILE A READ IS IN FLIGHT it shows the visitor's pick, so the control acknowledges the click
   * immediately instead of looking broken. ONCE THE READ SETTLES it shows the currency the figures
   * are actually in — which is the server's answer on success, and the previous currency when the
   * re-read failed. So the control and the numbers beneath it always agree, and there is no state
   * in which the page is stuck "converting" to a currency it will never be given.
   */
  const selected = reading ? (requested ?? currency) : currency

  /** The pick has not been priced yet, so NO figure on screen is denominated in it. */
  const awaitingCurrency = selected !== currency

  /** Record the visitor's pick and re-read, even if they picked the same code again. */
  const chooseCurrency = (code) => {
    setRequested(code)
    setAttempt((count) => count + 1)
  }

  const symbol =
    currency === BASE_CURRENCY
      ? RUPEE
      : (serverPlans.pro?.currency_symbol ?? catalogue?.currency_symbol ?? currency)

  /** Whether the figures on screen are the published list or a conversion of it. */
  const isConverted = currency !== BASE_CURRENCY

  /**
   * The options in the selector: the server's own list when it sent one.
   *
   * The visitor's current pick is appended when the list does not carry it, because a `<select>`
   * whose `value` matches no `<option>` silently displays the first one — so the control would
   * claim a currency the page is not showing.
   */
  const currencyOptions = useMemo(() => {
    const offered = catalogue?.supported_currencies
    const base =
      Array.isArray(offered) && offered.length > 0
        ? offered
            .filter((entry) => typeof entry?.code === 'string')
            .map((entry) => ({ code: entry.code, symbol: entry.symbol ?? entry.code }))
        : FALLBACK_CURRENCIES
    return base.some((option) => option.code === selected)
      ? base
      : [...base, { code: selected, symbol: selected }]
  }, [catalogue, selected])

  /**
   * The figure to print for a plan, and the precision to print it at.
   *
   * SERVER FIRST, PUBLISHED LIST AS THE FALLBACK. The server's `localized_price` is the amount
   * checkout will actually charge in that currency, so it is what a price list must quote. The
   * local rupee constants are used only when the read has not landed or failed — and only for the
   * base currency, because a rupee constant printed under a dollar sign would be a fabricated
   * conversion, which is the exact defect this component's history is about.
   */
  const priceFor = (plan) => {
    if (plan.inr === null) return null // quoted, not listed
    if (awaitingCurrency) return null // the pick has not been priced yet

    const server = serverPlans[plan.id]
    if (server) {
      const annual = server.annual
      if (isAnnual && annual) {
        return {
          amount: annual.monthly_equivalent,
          annualTotal: annual.localized_price,
          savings: annual.savings_percent,
          decimals: server.decimals,
        }
      }
      return {
        amount: server.localized_price,
        annualTotal: null,
        savings: null,
        decimals: server.decimals,
      }
    }

    // Not yet answered. Only the base currency can be served from the local list.
    if (isConverted) return null

    const monthly = monthlyPrice(plan)
    if (isAnnual && plan.annualInr) {
      return {
        amount: Math.round(plan.annualInr / 12),
        annualTotal: plan.annualInr,
        savings: monthly ? Math.round(100 - (plan.annualInr * 100) / (monthly * 12)) : null,
        decimals: 2,
      }
    }
    return { amount: monthly, annualTotal: null, savings: null, decimals: 2 }
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

              {/* The currency selector.
                  A native `<select>` on purpose: it is keyboard- and screen-reader-correct without
                  a custom listbox, and on mobile it opens the platform picker — which for a list of
                  twenty-odd currencies is better than anything rendered in-page.

                  The visitor's pick is NOT persisted here. Writing a currency preference needs a
                  session (`POST /api/billing/currency`), and this page is read by people who do not
                  have one; the preference is saved from the billing page once they sign in. */}
              <label className="ml-1 flex items-center gap-1.5 border-l border-line-default/80 pl-3">
                <Globe className="h-3.5 w-3.5 text-content-muted" aria-hidden="true" />
                <span className="sr-only">Display currency</span>
                <select
                  value={selected}
                  onChange={(event) => chooseCurrency(event.target.value)}
                  className="cursor-pointer rounded-lg bg-transparent py-1.5 pr-1 text-sm font-bold text-content-secondary transition-colors hover:text-content-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                >
                  {currencyOptions.map((option) => (
                    <option key={option.code} value={option.code}>
                      {option.code}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {/* WHAT KIND OF FIGURE IS ON SCREEN.
                The rupee list is a published commitment; every other currency is a conversion of
                it that moves with the exchange rate. Saying so is the difference between a price
                and an estimate, and a visitor comparing plans is entitled to know which they are
                reading. The server reports this per plan as `price_source`. */}
            <p className="mt-4 text-xs text-content-secondary" role="status">
              {awaitingCurrency ? (
                <>Converting prices to {selected}&hellip;</>
              ) : isConverted ? (
                <>
                  Prices are published in Indian Rupees ({RUPEE}) and shown in {currency} at
                  today&rsquo;s exchange rate. You are charged in {currency}.
                </>
              ) : (
                <>All prices in Indian Rupees ({RUPEE}).</>
              )}
            </p>
          </div>

          {/* ── The five cards ──────────────────────────────────────────── */}
          <div className="mx-auto grid max-w-7xl items-stretch gap-6 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {PLANS.map((plan) => {
              const priced = priceFor(plan)
              const isRecommended = plan.recommended
              const isQuoted = plan.inr === null
              // `priced === null` on a listed plan means the converted figure has not arrived yet.
              // The card renders its name, tagline and capabilities and leaves the price blank
              // rather than printing a rupee constant under another currency's symbol.
              const isPending = !isQuoted && priced === null

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
                    ) : isPending ? (
                      // A placeholder the width of a price, not a zero and not a rupee figure
                      // wearing the wrong symbol.
                      <div
                        className="h-10 w-28 animate-pulse rounded-md bg-surface-raised"
                        aria-label={`Loading the ${plan.name} price in ${selected}`}
                        role="status"
                      />
                    ) : (
                      <div className="flex items-baseline gap-1.5">
                        <span className="text-4xl font-black tracking-tight text-content-primary">
                          {symbol}
                          {formatMoney(priced.amount, priced.decimals)}
                        </span>
                        <span className="text-sm font-medium text-content-secondary">/month</span>
                      </div>
                    )}
                    {isAnnual && priced?.annualTotal ? (
                      <p className="mt-1.5 font-mono text-xs font-medium text-status-profit">
                        Billed {symbol}
                        {formatMoney(priced.annualTotal, priced.decimals)}/yr
                        {priced.savings ? ` — save ${priced.savings}%` : ''}
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
                        {/* The CTA quotes the SAME figure the card does, in the same currency.
                            It used to hardcode the rupee constant, which under a dollar heading
                            would have read "Start Automating — ₹499" beside "$5.20". */}
                        {priced && priced.amount > 0
                          ? ` — ${symbol}${formatMoney(priced.amount, priced.decimals)}`
                          : ''}
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
