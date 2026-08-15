import React, { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Check, Loader2 } from 'lucide-react'
import { api } from '../../api'

export default function Pricing() {
  const [isAnnual, setIsAnnual] = useState(false)
  const [currency, setCurrency] = useState('USD')
  const [plans, setPlans] = useState([])
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const loadPlans = async () => {
      try {
        const data = await api.billing.getPlans()
        setPlans(data?.plans || (Array.isArray(data) ? data : []))
      } catch (err) {
        console.error('Failed to load plans:', err)
      } finally {
        setIsLoading(false)
      }
    }
    loadPlans()
  }, [])

  const getDisplayPrice = (plan) => {
    const basePrice = currency === 'INR' ? plan.inr : plan.usd
    if (isAnnual && basePrice > 0) {
      return (basePrice * 12 * 0.8) / 12 // 20% discount on annual
    }
    return basePrice
  }

  const getAnnualPrice = (plan) => {
    const basePrice = currency === 'INR' ? plan.inr : plan.usd
    if (basePrice > 0) {
      return basePrice * 12 * 0.8
    }
    return 0
  }

  const currencySymbol = currency === 'INR' ? '₹' : '$'

  if (isLoading) {
    return (
      <section id="pricing" className="py-24 lg:py-32 border-t border-border-default/80">
        <div className="section-container">
          <div className="section-inner">
            <div className="flex justify-center items-center py-20">
              <Loader2 className="animate-spin text-accent-cyan" size={32} />
            </div>
          </div>
        </div>
      </section>
    )
  }
  
  return (
    <section id="pricing" className="py-24 lg:py-32 border-t border-border-default/80" aria-label="Pricing Tiers">
      <div className="section-container">
        <div className="section-inner">
          <div className="text-center mb-16">
            <div className="text-xs font-mono text-accent-cyan uppercase tracking-widest mb-3 font-semibold">Pricing</div>
            <h2 className="text-3xl sm:text-4xl lg:text-5xl font-black text-text-primary mb-4 tracking-tight">Infrastructure Tiers</h2>
            <p className="text-text-secondary max-w-xl mx-auto mb-8 text-base sm:text-lg">Billed monthly. Annual plans available with 20% reduction.</p>
            
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
            {/* Currency Toggle */}
            <div className="inline-flex items-center gap-3 p-1.5 rounded-2xl bg-bg-surface border border-border-default/80 shadow-md mt-4">
              <button 
                onClick={() => setCurrency('USD')} 
                className={`px-5 py-2.5 rounded-xl text-sm font-bold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan ${currency === 'USD' ? 'bg-accent-cyan text-text-inverse shadow-md' : 'text-text-secondary hover:text-text-primary'}`}
              >
                USD ($)
              </button>
              <button 
                onClick={() => setCurrency('INR')} 
                className={`px-5 py-2.5 rounded-xl text-sm font-bold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan ${currency === 'INR' ? 'bg-accent-cyan text-text-inverse shadow-md' : 'text-text-secondary hover:text-text-primary'}`}
              >
                INR (₹)
              </button>
            </div>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-8 max-w-6xl mx-auto items-stretch">
            {plans.map((plan) => {
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
                        {currencySymbol}{displayPrice.toLocaleString()}
                      </span>
                      <span className="text-text-muted text-sm font-medium">/month</span>
                    </div>
                    {isAnnual && !isFree && annualPrice > 0 && (
                      <p className="text-xs text-accent-profit mt-1.5 font-mono font-medium">Billed at {currencySymbol}{annualPrice.toLocaleString()}/yr — save 20%</p>
                    )}
                  </div>

                  <ul className="space-y-3.5 mb-8 flex-1">
                    {plan.features.map((feature, fi) => (
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
