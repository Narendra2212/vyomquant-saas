import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { Check } from 'lucide-react'

const tiers = [
  {
    name: 'Free',
    price: { monthly: 0, annual: 0 },
    description: 'Exploration and validation.',
    features: ['1 Paper Bot', '3 Backtests / Month', 'Basic Indicators', 'Browse Marketplace', 'Community Support', 'No Live Execution'],
    cta: 'Start Free',
    highlighted: false,
    badge: null,
  },
  {
    name: 'Pro',
    price: { monthly: 12, annual: 115.20 },
    description: 'Professional systematic execution.',
    features: ['5 Live Bots', 'Unlimited Backtests', 'Full Indicator Library', 'Clone Marketplace Strategies', 'Advanced Analytics', 'Email Support (48h SLA)'],
    cta: 'Select Pro',
    highlighted: true,
    badge: 'Most Popular',
  },
  {
    name: 'Elite',
    price: { monthly: 24, annual: 230.40 },
    description: 'Machine learning and research.',
    features: ['Unlimited Live Bots', '3 ML Training Slots', 'Custom Indicators', 'Publish to Marketplace', 'Signal Trace Visualization', 'Priority Support (SLA)'],
    cta: 'Select Elite',
    highlighted: false,
    badge: 'Quant Tier',
  },
]

export default function Pricing() {
  const [isAnnual, setIsAnnual] = useState(false)
  return (
    <section id="pricing" className="py-24 lg:py-32 border-t border-border-default">
      <div className="section-container">
        <div className="section-inner">
          <div className="text-center mb-16">
            <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider mb-3">Pricing</div>
            <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">Infrastructure Tiers</h2>
            <p className="text-text-secondary max-w-xl mx-auto mb-8">Billed monthly. Annual plans available with 20% reduction.</p>
            <div className="inline-flex items-center gap-4 p-1.5 rounded-xl bg-bg-elevated border border-border-default">
              <button onClick={() => setIsAnnual(false)} className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${!isAnnual ? 'bg-accent-cyan text-text-inverse' : 'text-text-secondary hover:text-text-primary'}`}>Monthly</button>
              <button onClick={() => setIsAnnual(true)} className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${isAnnual ? 'bg-accent-cyan text-text-inverse' : 'text-text-secondary hover:text-text-primary'}`}>
                Annual
                <span className="ml-1.5 text-xs bg-accent-profit-dim text-accent-profit px-1.5 py-0.5 rounded">Save 20%</span>
              </button>
            </div>
          </div>
          <div className="grid md:grid-cols-3 gap-6 max-w-5xl mx-auto">
            {tiers.map((tier) => (
              <div key={tier.name} className={`relative card-surface p-6 flex flex-col ${tier.highlighted ? 'border-accent-cyan/30 shadow-xl shadow-accent-cyan/5 lg:scale-105 z-10' : ''}`}>
                {tier.badge && (
                  <div className={`absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full text-xs font-bold ${tier.name === 'Elite' ? 'bg-accent-gold-dim text-accent-gold border border-accent-gold/20' : 'bg-accent-cyan-dim text-accent-cyan border border-accent-cyan/20'}`}>
                    {tier.badge}
                  </div>
                )}
                <div className="mb-6">
                  <h3 className="text-lg font-bold text-text-primary mb-1">{tier.name}</h3>
                  <p className="text-sm text-text-secondary">{tier.description}</p>
                </div>
                <div className="mb-6">
                  <div className="flex items-baseline gap-1">
                    <span className="text-4xl font-black text-text-primary">${isAnnual ? (tier.price.annual / 12).toFixed(0) : tier.price.monthly}</span>
                    <span className="text-text-muted text-sm">/month</span>
                  </div>
                  {isAnnual && tier.price.annual > 0 && (
                    <p className="text-xs text-text-muted mt-1 font-mono">Billed at ${tier.price.annual}/yr — save 20%</p>
                  )}
                </div>
                <ul className="space-y-3 mb-8 flex-1">
                  {tier.features.map((feature, fi) => (
                    <li key={fi} className="flex items-start gap-3 text-sm text-text-secondary">
                      <Check className="w-4 h-4 text-accent-profit flex-shrink-0 mt-0.5" />
                      {feature}
                    </li>
                  ))}
                </ul>
                <Link to="/signup" className={`w-full text-center py-3 rounded-xl font-semibold text-sm transition-all ${
                  tier.highlighted ? 'bg-accent-cyan text-text-inverse hover:shadow-lg hover:shadow-accent-cyan/30' :
                  tier.name === 'Elite' ? 'bg-accent-gold-dim text-accent-gold border border-accent-gold/20 hover:bg-accent-gold/20' :
                  'border border-border-default text-text-primary hover:bg-bg-elevated'
                }`}>
                  {tier.cta}
                </Link>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
