import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { Check } from 'lucide-react'

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * INR-ONLY PRICING — four published tiers
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * WHAT THIS SECTION SHOWS
 * -----------------------
 * Four tiers, priced in Indian Rupees only: ₹0, ₹499, ₹999, ₹2,499 per month.
 * There is no currency selector and no `$` anywhere on this surface — the
 * rupee is the one currency the landing page quotes.
 *
 * WHY THE FIGURES ARE DECLARED HERE RATHER THAN FETCHED
 * ----------------------------------------------------
 * These four numbers are the INR column of the canonical plan catalogue in
 * `backend_app/core/subscription_engine.py` (`pricing={"USD": …, "INR": 49900}`
 * and friends, in paise). `GET /api/billing/plans`, which this component used
 * to call, does NOT serve that column: `PricingService.get_localized_plans`
 * takes the USD cents figure and runs it through `FXService.localize_price`,
 * so an INR request comes back as an FX conversion of the dollar price
 * (~₹432 / ₹865 / ₹2,162 at the baseline rate) rather than the published
 * ₹499 / ₹999 / ₹2,499. Rendering that response would put a number on the
 * marketing page that contradicts the catalogue, and it would drift every time
 * the FX rate moved. A public price list is a published commitment, so it is
 * stated here as a constant and matches the catalogue exactly.
 *
 * The authenticated billing page (`src/pages/Billing.jsx`) still reads the
 * endpoint, because that surface has to show whatever the checkout will
 * actually charge.
 */
const PLANS = [
  {
    id: 'free',
    name: 'Free / Sandbox',
    description: 'Essential sandbox for systematic strategy design and forward paper testing.',
    inr: 0,
    recommended: false,
    features: [
      'Visual DAG Strategy Builder',
      'VectorBT Backtesting Engine',
      'Real-Time Paper Trading Mode',
      '1 Active Strategy Bot',
      'Standard Market Data Feeds',
    ]
  },
  {
    id: 'starter',
    name: 'Trader',
    description: 'For active systematic traders executing strategies on connected exchanges.',
    inr: 499,
    recommended: false,
    features: [
      'Everything in Free',
      '5 Active Strategy Bots',
      'Tick-level Historical Data',
      'CCXT.pro Multi-Exchange Routing',
      'Account Drawdown Circuit Breakers',
    ]
  },
  {
    id: 'pro',
    name: 'Pro Quant',
    description: 'High-capacity execution engine with machine learning models and priority routing.',
    inr: 999,
    recommended: true,
    features: [
      'Everything in Trader',
      '15 Active Strategy Bots',
      'XGBoost ML Node Training',
      'Custom Indicator Parameters',
      'Priority WebSocket Data Streams',
      'Priority Technical Support',
    ]
  },
  {
    id: 'enterprise',
    name: 'Institutional',
    description: 'Dedicated infrastructure, custom connectors, and multi-account risk management.',
    inr: 2499,
    recommended: false,
    features: [
      'Unlimited Strategy Bots',
      'Isolated VPC Execution Core',
      'Granular Role-Based Access',
      'Custom Risk Control Boundaries',
      'Direct Exchange Connectivity',
      '24/7 SLA & Dedicated Support',
    ]
  }
]

/** The one currency this section quotes. */
const RUPEE = '₹'

/**
 * Indian digit grouping (`2,499`, `1,23,456`), and a non-finite input renders
 * as `0` rather than throwing inside `toLocaleString` — the crash this
 * component's regression suite exists to pin down.
 */
const formatNumber = (value) => {
  const numericValue = Number(value)
  if (!Number.isFinite(numericValue)) return '0'
  return numericValue.toLocaleString('en-IN')
}

/** Monthly rupee figure for a tier, defensive against a malformed `inr`. */
const getBasePrice = (plan) => {
  const numericValue = Number(plan?.inr)
  return Number.isFinite(numericValue) ? numericValue : 0
}

export default function Pricing() {
  const [isAnnual, setIsAnnual] = useState(false)

  // Annual billing is the monthly rate less 20%, shown as a per-month figure so
  // the two toggle states stay comparable at a glance.
  const getDisplayPrice = (plan) => {
    const basePrice = getBasePrice(plan)
    if (isAnnual && basePrice > 0) {
      return Math.round((basePrice * 12 * 0.8) / 12)
    }
    return basePrice
  }

  const getAnnualPrice = (plan) => {
    const basePrice = getBasePrice(plan)
    return basePrice > 0 ? Math.round(basePrice * 12 * 0.8) : 0
  }

  return (
    <section id="pricing" className="py-24 lg:py-32 border-t border-border-default/80" aria-label="Pricing Tiers">
      <div className="section-container">
        <div className="section-inner">
          <div className="text-center mb-16">
            <div className="text-xs font-mono text-accent-cyan uppercase tracking-widest mb-3 font-semibold">Pricing</div>
            <h2 className="text-3xl sm:text-4xl lg:text-5xl font-black text-text-primary mb-4 tracking-tight">Infrastructure Tiers</h2>
            <p className="text-text-secondary max-w-xl mx-auto mb-8 text-base sm:text-lg">
              All prices in Indian Rupees ({RUPEE}). Billed monthly. Annual plans available with 20% reduction.
            </p>

            {/* Monthly / Annual Toggle */}
            <div className="inline-flex items-center gap-3 p-1.5 rounded-2xl bg-bg-surface border border-border-default/80 shadow-md">
              <button 
                onClick={() => setIsAnnual(false)} 
                className={`px-5 py-2.5 rounded-xl text-sm font-bold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan ${!isAnnual ? 'bg-accent-cyan text-text-inverse shadow-md' : 'text-text-secondary hover:text-text-primary'}`}
              >
                Monthly
              </button>
              <button 
                onClick={() => setIsAnnual(true)} 
                className={`px-5 py-2.5 rounded-xl text-sm font-bold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan ${isAnnual ? 'bg-accent-cyan text-text-inverse shadow-md' : 'text-text-secondary hover:text-text-primary'}`}
              >
                Annual
                <span className="ml-2 text-xs bg-accent-profit-dim text-accent-profit font-semibold px-2 py-0.5 rounded-md border border-accent-profit/20">Save 20%</span>
              </button>
            </div>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-8 max-w-6xl mx-auto items-stretch">
            {PLANS.map((plan) => {
              const displayPrice = getDisplayPrice(plan)
              const annualPrice = getAnnualPrice(plan)
              const isRecommended = plan.recommended
              const isFree = plan.id === 'free'
              
              return (
                <div 
                  key={plan.id} 
                  className={`relative card-surface p-8 flex flex-col h-full rounded-2xl transition-all duration-300 ${
                    isRecommended 
                      ? 'border-2 border-accent-cyan bg-bg-surface shadow-[0_0_50px_rgba(0,212,255,0.15)] lg:-translate-y-2 z-10' 
                      : 'border border-border-default/80 bg-bg-surface/70 hover:border-border-default hover:bg-bg-elevated/50'
                  }`}
                >
                  {isRecommended && (
                    <div className={`absolute -top-3.5 left-1/2 -translate-x-1/2 px-4 py-1 rounded-full text-xs font-extrabold tracking-wider uppercase shadow-md bg-accent-cyan text-text-inverse shadow-[0_0_15px_rgba(0,212,255,0.4)]`}>
                      Recommended
                    </div>
                  )}
                  
                  <div className="mb-6">
                    <h3 className="text-xl font-bold text-text-primary mb-1.5">{plan.name}</h3>
                    <p className="text-sm text-text-secondary">{plan.description}</p>
                  </div>

                  <div className="mb-6 pb-6 border-b border-border-default/60">
                    <div className="flex items-baseline gap-1.5">
                      <span className="text-4xl sm:text-5xl font-black text-text-primary tracking-tight">
                        {RUPEE}{formatNumber(displayPrice)}
                      </span>
                      <span className="text-text-muted text-sm font-medium">/month</span>
                    </div>
                    {isAnnual && !isFree && annualPrice > 0 && (
                      <p className="text-xs text-accent-profit mt-1.5 font-mono font-medium">Billed at {RUPEE}{formatNumber(annualPrice)}/yr — save 20%</p>
                    )}
                  </div>

                  <ul className="space-y-3.5 mb-8 flex-1">
                    {(Array.isArray(plan.features) ? plan.features : []).map((feature, fi) => (
                      <li key={fi} className="flex items-start gap-3 text-sm text-text-secondary font-medium">
                        <Check className="w-4.5 h-4.5 text-accent-profit flex-shrink-0 mt-0.5" />
                        {feature}
                      </li>
                    ))}
                  </ul>

                  <Link 
                    to="/signup" 
                    className={`w-full text-center py-3.5 rounded-xl font-bold text-sm transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan ${
                      isRecommended 
                        ? 'bg-accent-cyan text-text-inverse hover:bg-accent-cyan/90 shadow-[0_0_25px_rgba(0,212,255,0.3)] hover:shadow-[0_0_35px_rgba(0,212,255,0.45)]' 
                        : isFree
                        ? 'border border-border-default text-text-primary hover:bg-bg-elevated hover:border-accent-cyan/40'
                        : 'bg-accent-gold-dim text-accent-gold border border-accent-gold/30 hover:bg-accent-gold/20'
                    }`}
                  >
                    {isFree ? 'Start Free' : 'Get Started'}
                  </Link>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </section>
  )
}
