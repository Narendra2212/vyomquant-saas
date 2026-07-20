import React from 'react'
import { Link } from 'react-router-dom'
import { GitBranch, ArrowRight, MousePointer, Layers, Zap } from 'lucide-react'

export default function StrategyBuilderDemo() {
  return (
    <section id="builder" className="py-24 lg:py-32 border-t border-border-default">
      <div className="section-container">
        <div className="section-inner">
          <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-center">
            <div>
              <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider mb-3">Strategy Builder</div>
              <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">Visual DAG Canvas</h2>
              <p className="text-text-secondary leading-relaxed mb-6">
                Construct systematic rules via directed acyclic graph. Market data, technical indicators, ML models, and logic gates connect without programming. Export to production bot in one action.
              </p>
              <div className="space-y-4 mb-8">
                <div className="flex items-start gap-3">
                  <MousePointer className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">Drag-and-Drop Interface</div>
                    <div className="text-xs text-text-secondary">No programming required. Connect nodes visually.</div>
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <Layers className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">15+ Node Types</div>
                    <div className="text-xs text-text-secondary">Data feeds, indicators, logic gates, ML models, and execution nodes.</div>
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <Zap className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">One-Click Export</div>
                    <div className="text-xs text-text-secondary">Compile to production-ready bot instantly.</div>
                  </div>
                </div>
              </div>
              <Link to="/signup" className="btn-primary">
                Open Builder
                <ArrowRight className="w-4 h-4 ml-2" />
              </Link>
            </div>
            <div className="relative">
              <div className="terminal-frame">
                <div className="flex items-center gap-2 px-4 py-3 border-b border-border-default bg-bg-elevated/50">
                  <div className="flex gap-1.5">
                    <div className="w-3 h-3 rounded-full bg-accent-loss/80" />
                    <div className="w-3 h-3 rounded-full bg-accent-gold/80" />
                    <div className="w-3 h-3 rounded-full bg-accent-profit/80" />
                  </div>
                  <div className="flex-1 text-center">
                    <span className="text-xs font-mono text-text-muted">vyomquant — strategy-builder</span>
                  </div>
                  <div className="w-16" />
                </div>
                <div className="p-6 min-h-[360px] relative bg-bg-primary/50">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="card-surface p-4 border-l-2 border-l-accent-cyan">
                      <div className="text-xs font-mono text-text-muted mb-1">Data Feed</div>
                      <div className="text-sm font-semibold text-text-primary">BTC/USDT Tick</div>
                      <div className="text-xs font-mono text-accent-profit mt-1">WebSocket: Connected</div>
                    </div>
                    <div className="card-surface p-4 border-l-2 border-l-accent-gold">
                      <div className="text-xs font-mono text-text-muted mb-1">Indicator</div>
                      <div className="text-sm font-semibold text-text-primary">RSI(14) + EMA(50)</div>
                      <div className="text-xs font-mono text-text-secondary mt-1">Period: 1h</div>
                    </div>
                    <div className="card-surface p-4 border-l-2 border-l-accent-cyan">
                      <div className="text-xs font-mono text-text-muted mb-1">Logic Gate</div>
                      <div className="text-sm font-semibold text-text-primary">Crossover + Threshold</div>
                      <div className="text-xs font-mono text-text-secondary mt-1">AND condition</div>
                    </div>
                    <div className="card-surface p-4 border-l-2 border-l-accent-profit">
                      <div className="text-xs font-mono text-text-muted mb-1">Action</div>
                      <div className="text-sm font-semibold text-text-primary">Limit Buy + Stop</div>
                      <div className="text-xs font-mono text-accent-profit mt-1">Paper Mode</div>
                    </div>
                  </div>
                  <div className="absolute top-4 right-4 flex flex-col gap-2">
                    <div className="bg-bg-elevated/90 border border-border-default rounded-lg px-3 py-1.5 text-xs font-mono">
                      <span className="text-text-muted">Sharpe:</span>{' '}<span className="text-accent-cyan">2.1</span>
                    </div>
                    <div className="bg-bg-elevated/90 border border-border-default rounded-lg px-3 py-1.5 text-xs font-mono">
                      <span className="text-text-muted">Win Rate:</span>{' '}<span className="text-accent-profit">67.4%</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
