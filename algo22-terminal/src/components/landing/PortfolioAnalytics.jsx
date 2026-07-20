import React from 'react'
import { Link } from 'react-router-dom'
import { PieChart, ArrowRight, BarChart3, TrendingUp, Eye } from 'lucide-react'

export default function PortfolioAnalytics() {
  return (
    <section id="analytics" className="py-24 lg:py-32 border-t border-border-default bg-bg-surface/30">
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
                    <span className="text-xs font-mono text-text-muted">vyomquant — portfolio-analytics</span>
                  </div>
                  <div className="w-16" />
                </div>
                <div className="p-6 min-h-[360px]">
                  <div className="grid grid-cols-2 gap-4 mb-6">
                    <div className="card-surface p-4 text-center">
                      <div className="text-xs font-mono text-text-muted mb-1">Total P&L</div>
                      <div className="text-2xl font-black font-mono text-accent-profit">+18.4%</div>
                    </div>
                    <div className="card-surface p-4 text-center">
                      <div className="text-xs font-mono text-text-muted mb-1">Sharpe Ratio</div>
                      <div className="text-2xl font-black font-mono text-accent-cyan">2.14</div>
                    </div>
                    <div className="card-surface p-4 text-center">
                      <div className="text-xs font-mono text-text-muted mb-1">Max Drawdown</div>
                      <div className="text-2xl font-black font-mono text-accent-loss">-8.4%</div>
                    </div>
                    <div className="card-surface p-4 text-center">
                      <div className="text-xs font-mono text-text-muted mb-1">Win Rate</div>
                      <div className="text-2xl font-black font-mono text-accent-profit">67.4%</div>
                    </div>
                  </div>
                  <div className="flex items-center justify-center gap-8">
                    <div className="relative w-32 h-32">
                      <svg viewBox="0 0 36 36" className="w-full h-full -rotate-90">
                        <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="#1E2530" strokeWidth="3" />
                        <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="#00D4FF" strokeWidth="3" strokeDasharray="65, 100" />
                      </svg>
                      <div className="absolute inset-0 flex items-center justify-center">
                        <span className="text-sm font-mono text-text-primary">BTC</span>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <div className="flex items-center gap-2 text-xs font-mono"><div className="w-3 h-3 rounded bg-accent-cyan" /><span className="text-text-secondary">BTC 65%</span></div>
                      <div className="flex items-center gap-2 text-xs font-mono"><div className="w-3 h-3 rounded bg-accent-gold" /><span className="text-text-secondary">ETH 25%</span></div>
                      <div className="flex items-center gap-2 text-xs font-mono"><div className="w-3 h-3 rounded bg-accent-profit" /><span className="text-text-secondary">SOL 10%</span></div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div className="order-1 lg:order-2">
              <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider mb-3">Portfolio Analytics</div>
              <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">Real-Time Performance Attribution</h2>
              <p className="text-text-secondary leading-relaxed mb-6">
                Track P&L, Sharpe ratio, drawdown, and win rate across all deployed strategies. Portfolio-level analytics with asset allocation breakdowns.
              </p>
              <div className="space-y-4 mb-8">
                <div className="flex items-start gap-3">
                  <BarChart3 className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">Multi-Strategy Dashboard</div>
                    <div className="text-xs text-text-secondary">Aggregate metrics across all live and paper bots.</div>
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <TrendingUp className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">Equity Curve Tracking</div>
                    <div className="text-xs text-text-secondary">Visualize cumulative returns with benchmark overlays.</div>
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <Eye className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">Signal Trace</div>
                    <div className="text-xs text-text-secondary">Debug exactly which node fired which signal and when.</div>
                  </div>
                </div>
              </div>
              <Link to="/signup" className="btn-primary">
                View Analytics
                <ArrowRight className="w-4 h-4 ml-2" />
              </Link>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
