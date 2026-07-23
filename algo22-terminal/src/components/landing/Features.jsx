import React from 'react'
import { Link } from 'react-router-dom'
import { GitBranch, Activity, Bot, Brain, Shield, Store, ArrowRight } from 'lucide-react'

const featuresList = [
  { icon: GitBranch, title: 'Visual Strategy Builder', description: 'Construct systematic rules via directed acyclic graph. Market data, technical indicators, ML models, and logic gates connect without programming. Export to production bot in one action.', badge: null },
  { icon: Activity, title: 'VectorBT Backtesting Engine', description: 'Run multi-year tick-data backtests. Metrics include Sharpe ratio, Sortino ratio, Calmar ratio, maximum drawdown, profit factor, and expectancy.', badge: null },
  { icon: Bot, title: 'Paper Trading Mode', description: 'Forward-test against real exchange WebSocket feeds with simulated capital. Production latency. Zero capital risk.', badge: null },
  { icon: Brain, title: 'ML / XGBoost Node', description: 'Insert XGBoost classification directly into the strategy DAG. Train on historical data via managed infrastructure. No local Python environment required.', badge: 'Elite' },
  { icon: Shield, title: 'Institutional Risk Controls', description: 'Kill switch, drawdown monitor, daily loss limits, and position-size caps. Automatic halt on position drift beyond configured thresholds.', badge: null },
  { icon: Store, title: 'Strategy Marketplace', description: 'Discover, clone, and evaluate community strategies. Publish proprietary systems with live performance attribution.', badge: null },
]

export default function Features() {
  return (
    <section id="features" className="py-24 lg:py-32 border-t border-border-default/80 bg-bg-surface/20" aria-label="Core Capabilities">
      <div className="section-container">
        <div className="section-inner">
          <div className="text-center mb-16">
            <div className="text-xs font-mono text-accent-cyan uppercase tracking-widest mb-3 font-semibold">Core Capabilities</div>
            <h2 className="text-3xl sm:text-4xl lg:text-5xl font-black text-text-primary mb-4 tracking-tight">Infrastructure Components</h2>
            <p className="text-text-secondary max-w-xl mx-auto text-base sm:text-lg leading-relaxed">Six primary modules. Each addresses a specific systematic trading requirement.</p>
          </div>
          
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6 lg:gap-8">
            {featuresList.map((feature, i) => {
              const Icon = feature.icon
              return (
                <div 
                  key={i} 
                  className="group card-surface p-7 flex flex-col h-full rounded-2xl border border-border-default/80 hover:border-accent-cyan/40 bg-bg-surface/80 hover:bg-bg-elevated/60 transition-all duration-300 hover:-translate-y-1.5 shadow-md hover:shadow-2xl hover:shadow-accent-cyan/10"
                >
                  <div className="flex items-start justify-between mb-5">
                    <div className="w-12 h-12 rounded-xl bg-accent-cyan/10 border border-accent-cyan/20 flex items-center justify-center group-hover:scale-110 group-hover:bg-accent-cyan/20 transition-all duration-300">
                      <Icon className="w-6 h-6 text-accent-cyan" />
                    </div>
                    {feature.badge && (
                      <span className="text-xs font-mono font-bold text-accent-gold bg-accent-gold-dim border border-accent-gold/30 px-2.5 py-1 rounded-lg">
                        {feature.badge}
                      </span>
                    )}
                  </div>
                  <h3 className="text-lg font-bold text-text-primary mb-2.5 group-hover:text-accent-cyan transition-colors">{feature.title}</h3>
                  <p className="text-sm text-text-secondary leading-relaxed font-mono mt-auto">{feature.description}</p>
                </div>
              )
            })}
          </div>

          <div className="text-center mt-14">
            <Link 
              to="/docs" 
              className="inline-flex items-center gap-2 px-6 py-3 rounded-xl border border-border-default bg-bg-surface hover:bg-bg-elevated text-text-primary text-sm font-semibold hover:border-accent-cyan/40 transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-cyan"
            >
              View Full Capabilities
              <ArrowRight className="w-4 h-4" />
            </Link>
          </div>
        </div>
      </div>
    </section>
  )
}
