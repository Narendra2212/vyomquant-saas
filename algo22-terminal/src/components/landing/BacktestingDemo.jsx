import React from 'react'
import { Link } from 'react-router-dom'
import { Activity, ArrowRight, TrendingUp, BarChart3, Shield } from 'lucide-react'

export default function BacktestingDemo() {
  return (
    <section id="backtest" className="py-24 lg:py-32 border-t border-border-default bg-bg-surface/30">
      <div className="section-container">
        <div className="section-inner">
          <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-center">
            <div className="order-2 lg:order-1">
              <div className="terminal-frame">
                <div className="flex items-center gap-2 px-4 py-3 border-b border-border-default bg-bg-elevated/50">
                  <div className="flex gap-1.5">
                    <div className="w-3 h-3 rounded-full bg-accent-loss/80" />
                    <div className="w-3 h-3 rounded-full bg-accent-gold/80" />
                    <div className="w-3 h-3 rounded-full bg-accent-profit/80" />
                  </div>
                  <div className="flex-1 text-center">
                    <span className="text-xs font-mono text-text-muted">vyomquant — backtest-engine</span>
                  </div>
                  <div className="w-16" />
                </div>
                <div className="p-6 min-h-[360px]">
                  <div className="flex items-end gap-1 h-32 mb-6">
                    {[40,55,48,62,58,75,70,82,78,88,85,92,89,95,93,98,96,100,97,102].map((h,i) => (
                      <div key={i} className="flex-1 bg-accent-cyan/10 rounded-t-sm relative group">
                        <div className="absolute bottom-0 left-0 right-0 bg-accent-cyan/60 rounded-t-sm transition-all duration-300 group-hover:bg-accent-cyan" style={{ height: `${h}%` }} />
                      </div>
                    ))}
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    {[{label:'Sharpe',value:'2.14'},{label:'Sortino',value:'3.08'},{label:'Max Drawdown',value:'-8.4%'},{label:'Total Return',value:'+24.7%'}].map(m => (
                      <div key={m.label} className="card-surface p-3 text-center">
                        <div className="text-xs font-mono text-text-muted mb-1">{m.label}</div>
                        <div className="text-lg font-black font-mono text-text-primary">{m.value}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
            <div className="order-1 lg:order-2">
              <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider mb-3">Backtest Engine</div>
              <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">High-Fidelity Simulation</h2>
              <p className="text-text-secondary leading-relaxed mb-6">
                Run multi-year tick-data backtests. Metrics include Sharpe ratio, Sortino ratio, Calmar ratio, maximum drawdown, profit factor, and expectancy.
              </p>
              <div className="space-y-4 mb-8">
                <div className="flex items-start gap-3">
                  <TrendingUp className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">VectorBT Integration</div>
                    <div className="text-xs text-text-secondary">Institutional-grade backtesting with tick-level precision.</div>
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <BarChart3 className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">Monte Carlo Simulation</div>
                    <div className="text-xs text-text-secondary">Stress-test under hundreds of randomized scenarios.</div>
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <Shield className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">Walk Forward Analysis</div>
                    <div className="text-xs text-text-secondary">Out-of-sample validation to prevent overfitting.</div>
                  </div>
                </div>
              </div>
              <Link to="/signup" className="btn-primary">
                Run Backtest
                <ArrowRight className="w-4 h-4 ml-2" />
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
