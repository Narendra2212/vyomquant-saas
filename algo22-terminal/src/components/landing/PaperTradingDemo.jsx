import React from 'react'
import { Link } from 'react-router-dom'
import { Bot, ArrowRight, Wifi, Clock, ShieldCheck } from 'lucide-react'

export default function PaperTradingDemo() {
  return (
    <section id="paper" className="py-24 lg:py-32 border-t border-border-default">
      <div className="section-container">
        <div className="section-inner">
          <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-center">
            <div>
              <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider mb-3">Paper Trading</div>
              <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">Live Market Simulation</h2>
              <p className="text-text-secondary leading-relaxed mb-6">
                Forward-test against real exchange WebSocket feeds with simulated capital. Production latency. Zero capital risk.
              </p>
              <div className="space-y-4 mb-8">
                <div className="flex items-start gap-3">
                  <Wifi className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">Real Exchange Data</div>
                    <div className="text-xs text-text-secondary">Live WebSocket feeds from 50+ exchanges via CCXT.pro.</div>
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <Clock className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">Production Latency</div>
                    <div className="text-xs text-text-secondary">Experience real market conditions with simulated capital.</div>
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <ShieldCheck className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">Zero Risk</div>
                    <div className="text-xs text-text-secondary">Paper mode is the default. No real capital at risk.</div>
                  </div>
                </div>
              </div>
              <Link to="/signup" className="btn-primary">
                Start Paper Trading
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
                    <span className="text-xs font-mono text-text-muted">vyomquant — paper-monitor</span>
                  </div>
                  <div className="w-16" />
                </div>
                <div className="p-6 min-h-[360px]">
                  <div className="space-y-3">
                    {[
                      { name: 'RSI Mean Reversion', status: 'Running', pnl: '+2.4%', uptime: '14d 3h' },
                      { name: 'EMA Golden Cross', status: 'Running', pnl: '+1.8%', uptime: '7d 12h' },
                      { name: 'ML Hybrid Breakout', status: 'Paused', pnl: '-0.3%', uptime: '3d 8h' },
                    ].map(bot => (
                      <div key={bot.name} className="flex items-center justify-between p-3 card-surface">
                        <div className="flex items-center gap-3">
                          <div className={`w-2 h-2 rounded-full ${bot.status === 'Running' ? 'bg-accent-profit animate-pulse' : 'bg-accent-gold'}`} />
                          <div>
                            <div className="text-sm font-medium text-text-primary">{bot.name}</div>
                            <div className="text-xs font-mono text-text-muted">{bot.status} · {bot.uptime}</div>
                          </div>
                        </div>
                        <div className={`text-sm font-mono font-bold ${bot.pnl.startsWith('+') ? 'text-accent-profit' : 'text-accent-loss'}`}>{bot.pnl}</div>
                      </div>
                    ))}
                    <div className="flex items-center justify-between p-3 bg-accent-loss/5 border border-accent-loss/20 rounded-xl">
                      <div className="text-sm text-text-primary font-medium">Kill Switch</div>
                      <button className="px-3 py-1.5 bg-accent-loss/10 text-accent-loss text-xs font-bold rounded-lg hover:bg-accent-loss/20 transition-colors">EMERGENCY STOP</button>
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
