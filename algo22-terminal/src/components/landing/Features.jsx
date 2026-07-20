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
    <section id="features" className="py-24 lg:py-32 border-t border-border-default">
      <div className="section-container">
        <div className="section-inner">
          <div className="text-center mb-16">
            <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider mb-3">Core Capabilities</div>
            <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">Infrastructure Components</h2>
            <p className="text-text-secondary max-w-xl mx-auto">Six primary modules. Each addresses a specific systematic trading requirement.</p>
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {featuresList.map((feature, i) => {
              const Icon = feature.icon
              return (
                <div key={i} className="group card-surface p-6 hover:bg-bg-elevated/50 transition-all duration-300 hover:-translate-y-1 hover:shadow-xl hover:shadow-accent-cyan/5">
                  <div className="flex items-start justify-between mb-4">
                    <div className="w-10 h-10 rounded-xl bg-accent-cyan-dim flex items-center justify-center">
                      <Icon className="w-5 h-5 text-accent-cyan" />
                    </div>
                    {feature.badge && (
                      <span className="text-xs font-mono text-accent-gold bg-accent-gold-dim px-2 py-1 rounded-lg">{feature.badge}</span>
                    )}
                  </div>
                  <h3 className="text-base font-bold text-text-primary mb-2">{feature.title}</h3>
                  <p className="text-sm text-text-secondary leading-relaxed font-mono">{feature.description}</p>
                </div>
              )
            })}
          </div>
          <div className="text-center mt-12">
            <Link to="/docs" className="btn-ghost text-sm">
              View Full Capabilities
              <ArrowRight className="w-4 h-4 ml-2" />
            </Link>
          </div>
        </div>
      </div>
    </section>
  )
}
