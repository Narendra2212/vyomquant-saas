/**
 * AICopilot.jsx — Landing Page AI Copilot Showcase Component
 * 
 * NOTE: Intentionally DORMANT / UNMOUNTED. Preserved for future Copilot reactivation.
 */

import React from 'react'
import { Link } from 'react-router-dom'
import { Brain, ArrowRight, Sparkles, MessageSquare, Wand2 } from 'lucide-react'

export default function AICopilot() {
  return (
    <section id="copilot" className="py-24 lg:py-32 border-t border-border-default">
      <div className="section-container">
        <div className="section-inner">
          <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-center">
            <div>
              <div className="text-xs font-mono text-accent-cyan uppercase tracking-wider mb-3">AI Trading Copilot</div>
              <h2 className="text-3xl sm:text-4xl font-black text-text-primary mb-4">Intelligent Strategy Assistant</h2>
              <p className="text-text-secondary leading-relaxed mb-6">
                Natural language strategy generation. Describe your trading idea and the Copilot constructs the DAG, selects optimal indicators, and suggests risk parameters.
              </p>
              <div className="space-y-4 mb-8">
                <div className="flex items-start gap-3">
                  <MessageSquare className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">Natural Language Input</div>
                    <div className="text-xs text-text-secondary">"Build a mean reversion strategy for BTC using RSI and Bollinger Bands."</div>
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <Wand2 className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">Auto-Generated DAG</div>
                    <div className="text-xs text-text-secondary">Copilot constructs the full node graph with optimized parameters.</div>
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <Sparkles className="w-5 h-5 text-accent-cyan mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-sm font-medium text-text-primary">Risk Parameter Suggestions</div>
                    <div className="text-xs text-text-secondary">Intelligent stop-loss, position sizing, and drawdown limits.</div>
                  </div>
                </div>
              </div>
              <Link to="/signup" className="btn-primary">
                Try Copilot
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
                    <span className="text-xs font-mono text-text-muted">vyomquant — ai-copilot</span>
                  </div>
                  <div className="w-16" />
                </div>
                <div className="p-6 min-h-[360px] bg-bg-primary/50">
                  <div className="space-y-4">
                    <div className="flex gap-3">
                      <div className="w-8 h-8 rounded-full bg-bg-elevated flex items-center justify-center flex-shrink-0">
                        <span className="text-xs font-mono text-text-muted">U</span>
                      </div>
                      <div className="bg-bg-elevated rounded-xl rounded-tl-none px-4 py-3 max-w-[80%]">
                        <p className="text-sm text-text-primary">Build a mean reversion strategy for BTC using RSI and Bollinger Bands with a 2% stop loss.</p>
                      </div>
                    </div>
                    <div className="flex gap-3 justify-end">
                      <div className="bg-accent-cyan-dim rounded-xl rounded-tr-none px-4 py-3 max-w-[80%]">
                        <p className="text-sm text-text-primary">Generating DAG...</p>
                        <div className="mt-3 space-y-2">
                          <div className="flex items-center gap-2 text-xs font-mono text-text-secondary">
                            <Sparkles className="w-3 h-3 text-accent-cyan" />
                            <span>Node: RSI(14) + BB(20,2)</span>
                          </div>
                          <div className="flex items-center gap-2 text-xs font-mono text-text-secondary">
                            <Sparkles className="w-3 h-3 text-accent-cyan" />
                            <span>Logic: Oversold + Lower Band Touch</span>
                          </div>
                          <div className="flex items-center gap-2 text-xs font-mono text-text-secondary">
                            <Sparkles className="w-3 h-3 text-accent-cyan" />
                            <span>Risk: Stop 2%, Max Position 5%</span>
                          </div>
                        </div>
                      </div>
                      <div className="w-8 h-8 rounded-full bg-accent-cyan/20 flex items-center justify-center flex-shrink-0">
                        <Brain className="w-4 h-4 text-accent-cyan" />
                      </div>
                    </div>
                    <div className="flex gap-3 justify-end">
                      <div className="bg-accent-cyan-dim rounded-xl rounded-tr-none px-4 py-3">
                        <button className="text-xs font-mono text-accent-cyan hover:underline">Deploy to Paper →</button>
                      </div>
                      <div className="w-8 h-8 rounded-full bg-accent-cyan/20 flex items-center justify-center flex-shrink-0">
                        <Brain className="w-4 h-4 text-accent-cyan" />
                      </div>
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
