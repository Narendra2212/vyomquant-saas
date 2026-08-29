import React from 'react'
import { Network, Activity, Smartphone, ShieldCheck, Globe2, BarChart4 } from 'lucide-react'

export default function TrustSection() {
  const cards = [
    {
      icon: Network,
      title: 'Visual DAG Strategy Builder',
      desc: 'Construct complex trading logic visually. Drag and drop indicators, conditions, and execution blocks without writing code.'
    },
    {
      icon: Activity,
      title: 'VectorBT-Powered Backtesting',
      desc: 'Run millions of backtest iterations in seconds using vectorized operations, directly within the platform.'
    },
    {
      icon: Smartphone, // Reusing icon for environment metaphor
      title: 'Paper Trading Environment',
      desc: 'Test your strategies in a live market environment with zero capital risk using our real-time paper trading engine.'
    },
    {
      icon: ShieldCheck,
      title: 'Institutional Risk Controls',
      desc: 'Deploy automated circuit breakers, max drawdown limits, and portfolio-level risk parameters to safeguard your capital.'
    },
    {
      icon: Globe2,
      title: 'Multi-Exchange Connectivity',
      desc: 'Deploy to over 50+ global cryptocurrency exchanges through unified API routing and normalized data feeds.'
    },
    {
      icon: BarChart4,
      title: 'Institutional Portfolio Analytics',
      desc: 'Track Sharpe, Sortino, Max Drawdown, and deep performance metrics with professional grade charting.'
    }
  ]

  return (
    <section id="trust" className="py-24 lg:py-32 border-t border-border-default relative overflow-hidden">
      <div className="section-container relative z-10">
        <div className="section-inner">
          <div className="text-center mb-16">
            <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">
              Built for Serious Systematic Traders
            </h2>
            <p className="text-text-secondary max-w-2xl mx-auto text-base">
              VyomQuant provides the robust infrastructure demanded by institutional desks, wrapped in a completely visual interface.
            </p>
          </div>

          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-6">
            {cards.map((card, i) => {
              const Icon = card.icon
              return (
                <div key={i} className="card-surface p-8 border-l-2 border-l-border-default hover:border-l-accent-cyan transition-all duration-300">
                  <div className="w-12 h-12 rounded-xl bg-accent-cyan/10 flex items-center justify-center mb-6">
                    <Icon className="w-6 h-6 text-accent-cyan" />
                  </div>
                  <h3 className="text-lg font-bold text-text-primary mb-3">
                    {card.title}
                  </h3>
                  <p className="text-sm text-text-secondary leading-relaxed">
                    {card.desc}
                  </p>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </section>
  )
}
